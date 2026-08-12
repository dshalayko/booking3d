"""Парк: занятие, освобождение и состав.

Правила отсюда (PLAN.md):
  1. одна активная сессия на машину;
  2. одна активная сессия на человека;
  7. пока очередь непуста, занять свободную машину может только адресат
     предложения (и админ) — в пределах своего типа;
  8. таймер не освобождает машину автоматически — по истечении `eta_at`
     она уходит в `done_wait`, а не в `free`;
  9. активную работу снимает только владелец или админ; готовую деталь из
     `done_wait` может отметить любой авторизованный.

Состав парка (`create`, `rename`, `remove`) живёт здесь же, а не в админке:
командная строка и HTTP-обработчик должны одинаково отвечать на вопрос «можно
ли удалить эту машину», иначе одно из двух мест рано или поздно ответит иначе.

Функции не коммитят: транзакцией управляет вызывающий слой. Уведомления тоже
не отправляют — возвращают описание случившегося.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.enums import (
    ACTIVE_SESSION_STATUSES,
    MachineKind,
    MachineStatus,
    QueueStatus,
    SessionStatus,
)
from app.models import Machine, MachineSession, QueueEntry, User
from app.services import queue
from app.services.errors import (
    InvalidDuration,
    MachineHasHistory,
    MachineKindUnknown,
    MachineNameInvalid,
    MachineNameTaken,
    MachineNotAvailable,
    MachineReleaseForbidden,
    MachineReserved,
    NotAdmin,
    UserBusy,
)

MIN_DURATION_MINUTES = 15
MAX_DURATION_MINUTES = 48 * 60

MAX_NAME_LENGTH = 64


@dataclass(frozen=True)
class OccupyResult:
    session_id: int
    machine_id: int
    machine_name: str
    eta_at: datetime
    from_offer: bool


@dataclass(frozen=True)
class ReleaseResult:
    machine_id: int
    machine_name: str
    session_id: int | None
    owner_user_id: int | None
    session_status: str | None
    offers: list[queue.Offer] = field(default_factory=list)


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
    """Сколько следов машина оставила в журнале."""

    sessions: int
    offers: int

    @property
    def empty(self) -> bool:
        return not self.sessions and not self.offers


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
    machine = await _lock_machine(db, machine_id)

    if machine.status == MachineStatus.BROKEN:
        raise MachineNotAvailable(t.ERR_MACHINE_BROKEN.format(machine=machine.name))
    if machine.status != MachineStatus.FREE:
        raise MachineNotAvailable(t.ERR_MACHINE_BUSY.format(machine=machine.name))

    offer = await _check_queue_allows(db, user, machine)

    if await _active_session_of_user(db, user.id) is not None:
        raise UserBusy(t.ERR_USER_BUSY)

    # eta_at считается обычным сложением: работа идёт и ночью, ночная пауза
    # относится только к окну подтверждения предложения из очереди.
    session = MachineSession(
        machine_id=machine.id,
        user_id=user.id,
        started_at=now,
        eta_at=now + timedelta(minutes=duration_minutes),
        status=SessionStatus.PRINTING,
    )

    try:
        async with db.begin_nested():  # savepoint: гонку ловит уникальный индекс
            db.add(session)
            await db.flush()
    except IntegrityError as exc:
        raise MachineNotAvailable(t.ERR_MACHINE_JUST_TAKEN.format(machine=machine.name)) from exc

    machine.status = MachineStatus.PRINTING

    if offer is not None:
        offer.status = QueueStatus.TAKEN
        offer.resolved_at = now

    await db.flush()
    return OccupyResult(
        session_id=session.id,
        machine_id=machine.id,
        machine_name=machine.name,
        eta_at=session.eta_at,
        from_offer=offer is not None,
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

    offers = await queue.offer_free_machines(db, now)
    return ReleaseResult(
        machine_id=machine.id,
        machine_name=machine.name,
        session_id=session_id,
        owner_user_id=owner_user_id,
        session_status=session_status,
        offers=offers,
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
    """Вернуть машину в строй и сразу предложить её очереди её типа."""
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    now = now or _utcnow()
    machine = await _lock_machine(db, machine_id)

    if machine.status != MachineStatus.BROKEN:
        raise MachineNotAvailable(t.ERR_MACHINE_NOT_BROKEN.format(machine=machine.name))

    machine.status = MachineStatus.FREE
    machine.note = None
    await db.flush()

    offers = await queue.offer_free_machines(db, now)
    return ReleaseResult(
        machine_id=machine.id,
        machine_name=machine.name,
        session_id=None,
        owner_user_id=None,
        session_status=None,
        offers=offers,
    )


# --- состав парка ------------------------------------------------------------


async def list_machines(db: AsyncSession, kind: str | None = None) -> list[Machine]:
    """Парк в порядке id: сначала тот, кого завели раньше.

    Сортировка не по имени намеренно — на стене машины стоят в том же порядке,
    в каком их заводили, и «P2S #10» между «#1» и «#2» сбивал бы взгляд.
    """
    query = select(Machine).order_by(Machine.id)
    if kind is not None:
        query = query.where(Machine.kind == kind)
    return list((await db.scalars(query)).all())


async def create(db: AsyncSession, admin: User, name: str, kind: str) -> Machine:
    """Завести машину. Тип обязателен и потом не меняется.

    Смены типа нет и не планируется: на строку уже ссылается история, а
    «принтер, который вдруг стал гравировщиком» превратил бы прошлые печати в
    гравировки. Ошиблись при заведении — удалите, пока история пуста.
    """
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    name = _valid_name(name)
    if kind not in tuple(MachineKind):
        raise MachineKindUnknown(t.ERR_MACHINE_KIND_UNKNOWN.format(kind=kind))
    await _ensure_name_free(db, name)

    machine = Machine(name=name, kind=kind, status=MachineStatus.FREE)
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
    """Сколько сессий и приглашений ссылается на машину."""
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
    return Usage(sessions=sessions or 0, offers=offers or 0)


async def remove(db: AsyncSession, admin: User, machine_id: int) -> str:
    """Удалить машину — только пустую, без единой работы и приглашения.

    Машину с историей не удаляем: на неё ссылаются `sessions` и `queue`, и
    удаление либо упало бы на внешнем ключе, либо оторвало журнал от машины.
    Уехавшая машина с историей выводится в обслуживание — она останется на
    доске, но занять её будет нельзя. Возвращает имя удалённой.
    """
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    machine = await _get_machine(db, machine_id)
    counts = await usage(db, machine.id)
    if not counts.empty:
        raise MachineHasHistory(
            t.ERR_MACHINE_HAS_HISTORY.format(
                machine=machine.name, sessions=counts.sessions, offers=counts.offers
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


async def _check_queue_allows(db: AsyncSession, user: User, machine: Machine):
    """Правило 7: очередь имеет смысл, только если её нельзя обойти.

    Подошедший к киоску всегда быстрее того, кто едет из дома, поэтому пока
    очередь непуста, свободную машину занимает только адресат предложения.
    Проверяется в пределах типа: люди, ждущие гравировщик, не должны мешать
    занять освободившийся принтер. Возвращает предложение этого человека, если
    оно есть.
    """
    offer = await queue.offer_for_machine(db, machine.id)
    if offer is not None:
        if offer.user_id == user.id:
            return offer
        if user.is_admin:
            return None
        raise MachineReserved(t.ERR_MACHINE_RESERVED.format(machine=machine.name))

    if await queue.has_active_entries(db, kind=machine.kind) and not user.is_admin:
        raise MachineReserved(t.ERR_QUEUE_WAIT_YOUR_TURN)

    return None


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
