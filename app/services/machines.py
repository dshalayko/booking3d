"""Парк: занятие, освобождение и состав.

Правила отсюда (PLAN.md):
  1. одна активная сессия на машину;
  2. одна активная сессия на человека во всей системе;
  8. таймер не освобождает машину автоматически — по истечении `eta_at`
     она уходит в `done_wait`, а не в `free`;
  9. активную работу снимает только владелец или админ; готовую деталь из
     `done_wait` может отметить любой авторизованный;
 12. в своё окно машину занимает только тот, кто её забронировал, — и это право
     Сами брони живут в services/reservations.py.

Состав парка (`create`, `rename`, `remove`) живёт здесь же, а не в админке:
командная строка и HTTP-обработчик должны одинаково отвечать на вопрос «можно
ли удалить эту машину», иначе одно из двух мест рано или поздно ответит иначе.

Функции не коммитят: транзакцией управляет вызывающий слой. Уведомления тоже
не отправляют — возвращают описание случившегося.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.enums import (
    ACTIVE_SESSION_STATUSES,
    ROOM_KIND_MACHINE_KINDS,
    MachineKind,
    MachineStatus,
    SessionStatus,
)
from app.models import Machine, MachineSession, QueueEntry, Reservation, User
from app.services import reservations, rooms, schedule
from app.services.errors import (
    AlreadyBooked,
    InvalidDuration,
    MachineBooked,
    MachineHasHistory,
    MachineKindNotInRoom,
    MachineKindUnknown,
    MachineNameInvalid,
    MachineNameTaken,
    MachineNotAvailable,
    MachineReleaseForbidden,
    NotAdmin,
    UserBusy,
)

# Границы длительности лежат в services/schedule.py: они одни и те же у «занять
# сейчас» и у брони. Здесь оставлены имена, на которые ссылаются киоск и тесты.
MIN_DURATION_MINUTES = schedule.MIN_DURATION_MINUTES
MAX_DURATION_MINUTES = schedule.MAX_DURATION_MINUTES

MAX_NAME_LENGTH = 64


@dataclass(frozen=True)
class OccupyResult:
    session_id: int
    machine_id: int
    machine_name: str
    room_id: int
    eta_at: datetime
    from_reservation: bool = False


@dataclass(frozen=True)
class ReleaseResult:
    machine_id: int
    machine_name: str
    session_id: int | None
    owner_user_id: int | None
    session_status: str | None


@dataclass(frozen=True)
class DoneWaitResult:
    machine_id: int
    machine_name: str
    machine_kind: str
    session_id: int
    owner_user_id: int


@dataclass(frozen=True)
class BrokenResult:
    machine_id: int
    machine_name: str
    cancelled_session_id: int | None
    owner_user_id: int | None


@dataclass(frozen=True)
class Usage:
    """Сколько следов машина оставила в журнале.

    Брони считаются наравне с работами: на строку машины ссылаются и они, и
    удаление машины с бронью на завтра упало бы на внешнем ключе — то есть
    пятисотой вместо внятного отказа.
    """

    sessions: int
    offers: int
    bookings: int

    @property
    def empty(self) -> bool:
        return not self.sessions and not self.offers and not self.bookings


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def occupy(
    db: AsyncSession,
    user: User,
    machine_id: int,
    duration_minutes: int,
    now: datetime | None = None,
) -> OccupyResult:
    """Занять машину сейчас на указанную длительность."""
    if not MIN_DURATION_MINUTES <= duration_minutes <= MAX_DURATION_MINUTES:
        raise InvalidDuration(
            t.ERR_DURATION.format(
                min_minutes=MIN_DURATION_MINUTES, max_hours=MAX_DURATION_MINUTES // 60
            )
        )

    now = now or _utcnow()
    # Брони и занятия одного пользователя сериализуются одной строкой. Без
    # этого два параллельных запроса к разным машинам оба успеют пройти
    # проверку «пользователь свободен» до вставки сессии.
    await db.scalar(select(User.id).where(User.id == user.id).with_for_update())
    machine = await _lock_machine(db, machine_id)

    if machine.status == MachineStatus.BROKEN:
        raise MachineNotAvailable(t.ERR_MACHINE_BROKEN.format(machine=machine.name))
    if machine.status != MachineStatus.FREE:
        raise MachineNotAvailable(t.ERR_MACHINE_BUSY.format(machine=machine.name))

    reservation = await _check_booking_allows(db, user, machine, duration_minutes, now)
    # Работа одна во всей системе: физически одновременно пользоваться второй
    # машиной или переговорной тот же человек не может.
    if await _active_session_of_user(db, user.id) is not None:
        raise UserBusy(t.ERR_USER_BUSY)

    # Правило 13 с этой стороны: у человека с бронью во всей системе одно дело —
    # эта самая бронь. Своя забронированная машина не в счёт: её занимают и по
    # правилу 12 (пришёл в своё окно), и просто раньше времени, если она стоит
    # свободная, — это то же самое дело, а не второе. Чужая — уже второе, и
    # тогда один человек держит станок сейчас и час на будущее.
    if reservation is None:
        booking = await reservations.active_of_user(db, user.id)
        if booking is not None and booking.machine_id != machine.id:
            raise AlreadyBooked(t.ERR_OCCUPY_WHILE_BOOKED)

    # eta_at считается обычным сложением: работа может идти и ночью.
    session = MachineSession(
        machine_id=machine.id,
        room_id=machine.room_id,
        user_id=user.id,
        started_at=now,
        eta_at=now + timedelta(minutes=duration_minutes),
        status=SessionStatus.PRINTING,
        reservation_id=reservation.id if reservation is not None else None,
    )

    try:
        async with db.begin_nested():  # savepoint: гонку ловит уникальный индекс
            db.add(session)
            await db.flush()
    except IntegrityError as exc:
        raise MachineNotAvailable(t.ERR_MACHINE_JUST_TAKEN.format(machine=machine.name)) from exc

    machine.status = MachineStatus.PRINTING

    if reservation is not None:
        reservations.mark_taken(reservation, now)

    await db.flush()
    return OccupyResult(
        session_id=session.id,
        machine_id=machine.id,
        machine_name=machine.name,
        room_id=machine.room_id,
        eta_at=session.eta_at,
        from_reservation=reservation is not None,
    )


async def release(
    db: AsyncSession,
    actor: User,
    machine_id: int,
    now: datetime | None = None,
    reason: str | None = None,
) -> ReleaseResult:
    """Освободить машину: снятая деталь или прерванная работа.

    Правило 9: прервать активную работу может только её владелец или админ.
    Когда работа уже перешла в `done_wait`, стол можно освободить любым PIN:
    деталь готова, и парк не должен простаивать из-за того, что владелец уехал
    домой. Кто освободил, пишется в `freed_by_user_id`.

    Из `done_wait` сессия закрывается как `completed` (работа состоялась), из
    `printing` — как `cancelled` (сняли на середине).
    """
    now = now or _utcnow()
    machine = await _lock_machine(db, machine_id)

    if machine.status == MachineStatus.BROKEN:
        raise MachineNotAvailable(t.ERR_MACHINE_BROKEN.format(machine=machine.name))

    session = await _active_session_of_machine(db, machine.id)
    session_id: int | None = None
    owner_user_id: int | None = None
    session_status: str | None = None

    if session is not None:
        if (
            machine.status == MachineStatus.PRINTING
            and session.user_id != actor.id
            and not actor.is_admin
        ):
            raise MachineReleaseForbidden(t.ERR_MACHINE_RELEASE_FORBIDDEN)

        session_status = (
            SessionStatus.COMPLETED
            if machine.status == MachineStatus.DONE_WAIT
            else SessionStatus.CANCELLED
        )
        session.status = session_status
        session.ended_at = now
        session.freed_by_user_id = actor.id
        session.cancel_reason = reason
        session_id = session.id
        owner_user_id = session.user_id

    machine.status = MachineStatus.FREE
    await db.flush()

    return ReleaseResult(
        machine_id=machine.id,
        machine_name=machine.name,
        session_id=session_id,
        owner_user_id=owner_user_id,
        session_status=session_status,
    )


async def mark_done_wait(
    db: AsyncSession, machine_id: int, now: datetime | None = None
) -> DoneWaitResult:
    """Истёк `eta_at`: работа считается законченной, деталь ещё на столе.

    Правило 8: автоматически освобождать машину по таймеру нельзя — оценка
    длительности всегда врёт, и освобождение вслепую приведёт к двум людям,
    работающим на один стол.
    """
    now = now or _utcnow()
    machine = await _lock_machine(db, machine_id)

    if machine.status != MachineStatus.PRINTING:
        raise MachineNotAvailable(t.ERR_MACHINE_NOT_WORKING.format(machine=machine.name))

    session = await _active_session_of_machine(db, machine.id)
    if session is None:
        raise MachineNotAvailable(t.ERR_MACHINE_NO_SESSION.format(machine=machine.name))

    session.status = SessionStatus.DONE_WAIT
    machine.status = MachineStatus.DONE_WAIT
    await db.flush()

    return DoneWaitResult(
        machine_id=machine.id,
        machine_name=machine.name,
        machine_kind=machine.kind,
        session_id=session.id,
        owner_user_id=session.user_id,
    )


async def set_broken(
    db: AsyncSession,
    admin: User,
    machine_id: int,
    note: str | None = None,
    now: datetime | None = None,
) -> BrokenResult:
    """Вывести машину из строя. Активная работа снимается."""
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    now = now or _utcnow()
    machine = await _lock_machine(db, machine_id)

    session = await _active_session_of_machine(db, machine.id)
    cancelled_id: int | None = None
    owner_user_id: int | None = None
    if session is not None:
        session.status = SessionStatus.CANCELLED
        session.ended_at = now
        session.freed_by_user_id = admin.id
        session.cancel_reason = note or t.REASON_MACHINE_BROKEN
        cancelled_id = session.id
        owner_user_id = session.user_id

    machine.status = MachineStatus.BROKEN
    machine.note = note
    await db.flush()

    return BrokenResult(
        machine_id=machine.id,
        machine_name=machine.name,
        cancelled_session_id=cancelled_id,
        owner_user_id=owner_user_id,
    )


async def clear_broken(
    db: AsyncSession, admin: User, machine_id: int, now: datetime | None = None
) -> ReleaseResult:
    """Вернуть машину в строй."""
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    now = now or _utcnow()
    machine = await _lock_machine(db, machine_id)

    if machine.status != MachineStatus.BROKEN:
        raise MachineNotAvailable(t.ERR_MACHINE_NOT_BROKEN.format(machine=machine.name))

    machine.status = MachineStatus.FREE
    machine.note = None
    await db.flush()

    return ReleaseResult(
        machine_id=machine.id,
        machine_name=machine.name,
        session_id=None,
        owner_user_id=None,
        session_status=None,
    )


# --- состав парка ------------------------------------------------------------


async def get(db: AsyncSession, machine_id: int) -> Machine:
    """Машина по номеру или отказ «нет такой». Публичная пара к `_get_machine`:
    имя машины нужно и экранам, а не только правилам внутри модуля."""
    return await _get_machine(db, machine_id)


async def list_machines(
    db: AsyncSession, room_id: int | None = None, kind: str | None = None
) -> list[Machine]:
    """Парк в порядке id: сначала тот, кого завели раньше.

    Сортировка не по имени намеренно — на стене машины стоят в том же порядке,
    в каком их заводили, и «P2S #10» между «#1» и «#2» сбивал бы взгляд.
    """
    query = select(Machine).order_by(Machine.id)
    if room_id is not None:
        query = query.where(Machine.room_id == room_id)
    if kind is not None:
        query = query.where(Machine.kind == kind)
    return list((await db.scalars(query)).all())


async def create(
    db: AsyncSession, admin: User, room_id: int, name: str, kind: str
) -> Machine:
    """Завести машину в помещении. Тип обязателен и потом не меняется.

    Смены типа нет и не планируется: на строку уже ссылается история, а
    «принтер, который вдруг стал гравировщиком» превратил бы прошлые печати в
    гравировки. Ошиблись при заведении — удалите, пока история пуста.

    Помещения машина тоже не меняет — по той же причине: `sessions`, `queue` и
    `reservations` держат его копией (см. app/models.py), и переезд задним числом
    переписал бы прошлое. Уехавшую машину выводят в обслуживание, а на новом
    месте заводят заново.

    Тип должен подходить помещению (`ROOM_KIND_MACHINE_KINDS`): принтер в
    переговорной сделал бы из её расписания расписание печати.
    """
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    name = _valid_name(name)
    if kind not in tuple(MachineKind):
        raise MachineKindUnknown(t.ERR_MACHINE_KIND_UNKNOWN.format(kind=kind))

    room = await rooms.get(db, room_id)
    if kind not in ROOM_KIND_MACHINE_KINDS.get(room.kind, ()):
        raise MachineKindNotInRoom(
            t.ERR_MACHINE_KIND_NOT_IN_ROOM.format(
                kind=t.MACHINE_KIND_ONE.get(kind, kind), room=room.name
            )
        )
    await _ensure_name_free(db, name)

    machine = Machine(room_id=room.id, name=name, kind=kind, status=MachineStatus.FREE)
    db.add(machine)
    try:
        await db.flush()
    except IntegrityError as exc:  # имя заняли между проверкой и вставкой
        raise MachineNameTaken(t.ERR_MACHINE_NAME_TAKEN.format(name=name)) from exc
    return machine


async def rename(db: AsyncSession, admin: User, machine_id: int, name: str) -> str:
    """Переименовать машину. Возвращает прежнее имя.

    История остаётся: меняется имя той же строки, а не создаётся новая. Поэтому
    в журнале прошлые работы покажутся уже под новым именем — это осознанно,
    физическая машина в том же углу, и два имени в журнале путали бы сильнее.
    """
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    name = _valid_name(name)
    machine = await _get_machine(db, machine_id)
    if machine.name == name:
        return machine.name

    await _ensure_name_free(db, name, except_id=machine.id)
    previous = machine.name
    machine.name = name
    try:
        await db.flush()
    except IntegrityError as exc:
        raise MachineNameTaken(t.ERR_MACHINE_NAME_TAKEN.format(name=name)) from exc
    return previous


async def usage(db: AsyncSession, machine_id: int) -> Usage:
    """Сколько работ, приглашений и брон ссылается на машину."""
    sessions = await db.scalar(
        select(func.count())
        .select_from(MachineSession)
        .where(MachineSession.machine_id == machine_id)
    )
    offers = await db.scalar(
        select(func.count())
        .select_from(QueueEntry)
        .where(QueueEntry.offered_machine_id == machine_id)
    )
    bookings = await db.scalar(
        select(func.count()).select_from(Reservation).where(Reservation.machine_id == machine_id)
    )
    return Usage(sessions=sessions or 0, offers=offers or 0, bookings=bookings or 0)


async def remove(db: AsyncSession, admin: User, machine_id: int) -> str:
    """Удалить машину — только пустую, без единой работы, брони и приглашения.

    Машину с историей не удаляем: на неё ссылаются `sessions`, `queue` и
    `reservations`, и удаление либо упало бы на внешнем ключе, либо оторвало
    журнал от машины. Уехавшая машина с историей выводится в обслуживание — она
    останется на доске, но занять её будет нельзя. Возвращает имя удалённой.
    """
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    machine = await _get_machine(db, machine_id)
    counts = await usage(db, machine.id)
    if not counts.empty:
        raise MachineHasHistory(
            t.ERR_MACHINE_HAS_HISTORY.format(
                machine=machine.name,
                sessions=counts.sessions,
                offers=counts.offers,
                bookings=counts.bookings,
            )
        )

    name = machine.name
    await db.delete(machine)
    await db.flush()
    return name


# --- внутреннее --------------------------------------------------------------


def _valid_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise MachineNameInvalid(t.ERR_MACHINE_NAME_EMPTY)
    if len(name) > MAX_NAME_LENGTH:
        raise MachineNameInvalid(t.ERR_MACHINE_NAME_LONG.format(limit=MAX_NAME_LENGTH))
    return name


async def _ensure_name_free(db: AsyncSession, name: str, except_id: int | None = None) -> None:
    taken = await db.scalar(select(Machine.id).where(Machine.name == name))
    if taken is not None and taken != except_id:
        raise MachineNameTaken(t.ERR_MACHINE_NAME_TAKEN.format(name=name))


async def _check_booking_allows(
    db: AsyncSession,
    user: User,
    machine: Machine,
    duration_minutes: int,
    now: datetime,
) -> Reservation | None:
    """Правило 12: бронь — это право на машину в конкретные часы.

    Три случая. Идёт чужое окно — занять нельзя вовсе, даже админу мимо чужой
    брони: он снимает бронь отдельным действием, чтобы это осталось в журнале.
    Идёт своё окно — можно, и возвращённая бронь пометится сыгравшей. Окна нет —
    можно, но не дольше, чем до начала ближайшей брони: иначе восьмичасовая
    печать сожрёт чужой забронированный час, и правило 8 (таймер не освобождает
    машину сам) не даст его вернуть.
    """
    current = await reservations.current_for_machine(db, machine.id, now)
    if current is not None:
        if current.user_id == user.id:
            return current
        raise MachineBooked(
            t.ERR_MACHINE_BOOKED_NOW.format(
                machine=machine.name, time=_hhmm(current.ends_at)
            )
        )

    upcoming = await reservations.next_for_machine(db, machine.id, now)
    if upcoming is None:
        return None

    available = int((upcoming.starts_at - now).total_seconds() // 60)
    if duration_minutes > available:
        raise MachineBooked(
            t.ERR_MACHINE_BOOKED_LATER.format(
                machine=machine.name,
                time=_hhmm(upcoming.starts_at),
                minutes=max(0, available),
            )
        )
    return None


def _hhmm(value: datetime) -> str:
    return schedule.local(value).strftime(t.TIME_FORMAT)


async def _get_machine(db: AsyncSession, machine_id: int) -> Machine:
    machine = await db.get(Machine, machine_id)
    if machine is None:
        raise MachineNotAvailable(t.ERR_MACHINE_NOT_FOUND)
    return machine


async def _lock_machine(db: AsyncSession, machine_id: int) -> Machine:
    machine = await db.get(Machine, machine_id, with_for_update=True)
    if machine is None:
        raise MachineNotAvailable(t.ERR_MACHINE_NOT_FOUND)
    return machine


async def _active_session_of_machine(db: AsyncSession, machine_id: int) -> MachineSession | None:
    return await db.scalar(
        select(MachineSession).where(
            MachineSession.machine_id == machine_id,
            MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )


async def _active_session_of_user(db: AsyncSession, user_id: int) -> MachineSession | None:
    return await db.scalar(
        select(MachineSession).where(
            MachineSession.user_id == user_id,
            MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )
