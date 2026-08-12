"""Брони на будущее.

Очередь (services/queue.py) отвечает на вопрос «кто следующий, когда
освободится». Бронь отвечает на другой: «мне нужно к утру четверга». Поэтому
бронь всегда на конкретную машину и на конкретное окно, а не на тип.

Правила отсюда (PLAN.md):
  12. в своё окно машину занимает только тот, кто её забронировал, — это право
      сильнее очереди (правило 7);
  13. одна активная бронь на человека;
  14. бронь не сгорает, пока машина занята чужой работой: отсчёт неявки идёт
      только со свободной машины.

Непересечение окон держит EXCLUDE-ограничение в БД (миграция 0006), а не
проверка в коде: двое, жмущие «забронировать» на один час, — обычная гонка.
Проверка запросом здесь тоже есть, но ради внятного отказа, а не ради
корректности.

Модуль не зависит от services/machines.py, хотя оба работают с теми же
строками: `machines.occupy` спрашивает у брон, до какого часа машину можно
занять, и обратный импорт замкнул бы кольцо. Цена — свой `_lock_machine` на
четыре строки; выигрыш — направление зависимостей, которое видно с первого
взгляда.

Как и остальной домен, модуль не коммитит и ничего не отправляет: возвращает
описание случившегося, а транзакцией и Telegram занимается вызывающий слой.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.config import settings
from app.enums import (
    ACTIVE_RESERVATION_STATUSES,
    ACTIVE_SESSION_STATUSES,
    MachineStatus,
    ReservationStatus,
    SessionStatus,
)
from app.models import Machine, MachineSession, Reservation, User
from app.services import queue as queue_svc
from app.services import schedule
from app.services.errors import (
    AlreadyBooked,
    InvalidDuration,
    InvalidReservationTime,
    MachineNotAvailable,
    ReservationForbidden,
    ReservationNotFound,
    ReservationOverlap,
)

# Состояния клетки расписания: уезжают в CSS-класс и в текст подписи.
CELL_FREE = "free"
CELL_PAST = "past"
CELL_BUSY = "busy"
CELL_BOOKED = "booked"
CELL_MINE = "mine"
CELL_BROKEN = "broken"


@dataclass(frozen=True)
class BookResult:
    reservation_id: int
    machine_id: int
    machine_name: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class CancelResult:
    reservation_id: int
    user_id: int
    machine_id: int
    machine_name: str
    starts_at: datetime
    by_owner: bool
    offers: list[queue_svc.Offer] = field(default_factory=list)


@dataclass(frozen=True)
class ExpireResult:
    reservation_id: int
    user_id: int
    machine_id: int
    machine_name: str
    starts_at: datetime
    offers: list[queue_svc.Offer] = field(default_factory=list)


@dataclass(frozen=True)
class Cell:
    """Один слот одной машины в расписании дня."""

    starts_at: datetime
    label: str
    state: str
    who: str | None = None
    reservation_id: int | None = None
    # Продолжение того же занятия, что часом выше: имя во всех клетках подряд
    # превращает столбец в стену текста, а границу окна найти труднее.
    continues: bool = False

    @property
    def bookable(self) -> bool:
        return self.state == CELL_FREE


@dataclass(frozen=True)
class MachineColumn:
    machine_id: int
    name: str
    cells: list[Cell]


@dataclass(frozen=True)
class DaySchedule:
    kind: str
    day: date
    days: list[schedule.DayOption]
    columns: list[MachineColumn]
    hours: list[str]

    @property
    def has_free(self) -> bool:
        return any(cell.bookable for column in self.columns for cell in column.cells)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- действия ----------------------------------------------------------------


async def book(
    db: AsyncSession,
    user: User,
    machine_id: int,
    starts_at: datetime,
    duration_minutes: int,
    now: datetime | None = None,
) -> BookResult:
    """Забронировать машину на окно в будущем."""
    now = now or _utcnow()

    if not (
        settings.reservation_min_minutes
        <= duration_minutes
        <= schedule.MAX_DURATION_MINUTES
    ):
        raise InvalidDuration(
            t.ERR_RESERVATION_DURATION.format(
                min_minutes=settings.reservation_min_minutes,
                max_hours=schedule.MAX_DURATION_MINUTES // 60,
            )
        )

    starts_at = starts_at.astimezone(UTC)
    if not schedule.is_aligned(starts_at):
        raise InvalidReservationTime(
            t.ERR_RESERVATION_NOT_ALIGNED.format(step=settings.reservation_slot_minutes)
        )
    if starts_at <= now:
        raise InvalidReservationTime(t.ERR_RESERVATION_PAST)
    if starts_at > schedule.horizon_end(now):
        raise InvalidReservationTime(
            t.ERR_RESERVATION_HORIZON.format(days=settings.reservation_horizon_days)
        )

    ends_at = starts_at + timedelta(minutes=duration_minutes)
    machine = await _lock_machine(db, machine_id)
    if machine.status == MachineStatus.BROKEN:
        raise MachineNotAvailable(t.ERR_MACHINE_BROKEN.format(machine=machine.name))

    if await active_of_user(db, user.id) is not None:
        raise AlreadyBooked(t.ERR_ALREADY_BOOKED)

    await _ensure_window_free(db, machine, starts_at, ends_at)

    reservation = Reservation(
        machine_id=machine.id,
        user_id=user.id,
        starts_at=starts_at,
        ends_at=ends_at,
        status=ReservationStatus.BOOKED,
        created_at=now,
    )
    try:
        # Savepoint: гонку двух бронирований ловят ограничения БД, а не проверки
        # выше — те нужны только ради внятного текста отказа.
        async with db.begin_nested():
            db.add(reservation)
            await db.flush()
    except IntegrityError as exc:
        raise _integrity_error(exc, machine.name) from exc

    return BookResult(
        reservation_id=reservation.id,
        machine_id=machine.id,
        machine_name=machine.name,
        starts_at=starts_at,
        ends_at=ends_at,
    )


async def cancel(
    db: AsyncSession,
    actor: User,
    reservation_id: int,
    reason: str | None = None,
    now: datetime | None = None,
) -> CancelResult:
    """Отменить бронь. Свою — сам, чужую — только админ."""
    now = now or _utcnow()

    reservation = await db.get(Reservation, reservation_id, with_for_update=True)
    if reservation is None or reservation.status != ReservationStatus.BOOKED:
        raise ReservationNotFound(t.ERR_RESERVATION_NOT_FOUND)

    by_owner = reservation.user_id == actor.id
    if not by_owner and not actor.is_admin:
        raise ReservationForbidden(t.ERR_RESERVATION_FORBIDDEN)

    machine = await db.get(Machine, reservation.machine_id)
    reservation.status = ReservationStatus.CANCELLED
    reservation.resolved_at = now
    reservation.cancel_reason = reason
    reservation.cancelled_by_user_id = actor.id
    await db.flush()

    # Окно могло идти прямо сейчас — тогда до отмены машина была придержана
    # бронью, а теперь её можно предложить очереди.
    offers = await queue_svc.offer_free_machines(db, now)
    return CancelResult(
        reservation_id=reservation.id,
        user_id=reservation.user_id,
        machine_id=reservation.machine_id,
        machine_name=machine.name if machine else t.BOT_MACHINE_FALLBACK,
        starts_at=reservation.starts_at,
        by_owner=by_owner,
        offers=offers,
    )


async def expire_no_show(
    db: AsyncSession, reservation_id: int, now: datetime | None = None
) -> ExpireResult:
    """Человек не пришёл: закрыть бронь и отдать машину очереди.

    Правило 14: отсчёт идёт только со свободной машины. Если на столе чужая
    незабранная деталь, бронь ждёт — иначе человек теряет своё окно из-за чужой
    невнимательности, не сделав ничего неправильно.
    """
    now = now or _utcnow()

    reservation = await db.get(Reservation, reservation_id, with_for_update=True)
    if reservation is None or reservation.status != ReservationStatus.BOOKED:
        raise ReservationNotFound(t.ERR_RESERVATION_NOT_FOUND)

    deadline = reservation.starts_at + timedelta(minutes=settings.reservation_grace_minutes)
    if now < deadline:
        raise ReservationNotFound(t.ERR_RESERVATION_WINDOW_OPEN)

    machine = await _lock_machine(db, reservation.machine_id)
    if machine.status != MachineStatus.FREE:
        raise ReservationNotFound(t.ERR_RESERVATION_MACHINE_BUSY.format(machine=machine.name))

    reservation.status = ReservationStatus.EXPIRED
    reservation.resolved_at = now
    await db.flush()

    offers = await queue_svc.offer_free_machines(db, now)
    return ExpireResult(
        reservation_id=reservation.id,
        user_id=reservation.user_id,
        machine_id=machine.id,
        machine_name=machine.name,
        starts_at=reservation.starts_at,
        offers=offers,
    )


def mark_taken(reservation: Reservation, now: datetime) -> None:
    """Человек пришёл и занял машину — бронь сыграла.

    Переход живёт здесь, а не в services/machines.py, чтобы все смены статуса
    брони читались в одном файле.
    """
    reservation.status = ReservationStatus.TAKEN
    reservation.resolved_at = now


# --- запросы -----------------------------------------------------------------


async def get_active(db: AsyncSession, reservation_id: int) -> Reservation:
    """Незакрытая бронь по номеру — для экранов отмены."""
    reservation = await db.get(Reservation, reservation_id)
    if reservation is None or reservation.status != ReservationStatus.BOOKED:
        raise ReservationNotFound(t.ERR_RESERVATION_NOT_FOUND)
    return reservation


async def active_of_user(db: AsyncSession, user_id: int) -> Reservation | None:
    return await db.scalar(
        select(Reservation)
        .where(
            Reservation.user_id == user_id,
            Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
        )
        .order_by(Reservation.starts_at)
    )


async def current_for_machine(
    db: AsyncSession, machine_id: int, now: datetime | None = None
) -> Reservation | None:
    """Бронь, окно которой идёт прямо сейчас."""
    now = now or _utcnow()
    return await db.scalar(
        select(Reservation).where(
            Reservation.machine_id == machine_id,
            Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
            Reservation.starts_at <= now,
            Reservation.ends_at > now,
        )
    )


async def next_for_machine(
    db: AsyncSession, machine_id: int, now: datetime | None = None
) -> Reservation | None:
    """Ближайшая бронь впереди — ею урезается «занять сейчас»."""
    now = now or _utcnow()
    return await db.scalar(
        select(Reservation)
        .where(
            Reservation.machine_id == machine_id,
            Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
            Reservation.starts_at > now,
        )
        .order_by(Reservation.starts_at)
        .limit(1)
    )


async def slot_taken(db: AsyncSession, machine_id: int, moment: datetime) -> bool:
    """Занят ли этот час: чужой бронью или идущей работой.

    Нужна экранам: показывать форму брони на занятый час нельзя — человек введёт
    PIN, выберет длительность и только тогда узнает, что час не его.
    """
    booking = await db.scalar(
        select(Reservation.id).where(
            Reservation.machine_id == machine_id,
            Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
            Reservation.starts_at <= moment,
            Reservation.ends_at > moment,
        )
    )
    if booking is not None:
        return True

    session = await db.scalar(
        select(MachineSession.id).where(
            MachineSession.machine_id == machine_id,
            MachineSession.status == SessionStatus.PRINTING,
            MachineSession.eta_at > moment,
        )
    )
    return session is not None


async def free_minutes(
    db: AsyncSession, machine_id: int, now: datetime | None = None
) -> int | None:
    """Сколько минут машину можно занять прямо сейчас. None — без ограничения."""
    now = now or _utcnow()
    upcoming = await next_for_machine(db, machine_id, now)
    if upcoming is None:
        return None
    return int((upcoming.starts_at - now).total_seconds() // 60)


async def upcoming_for_machines(
    db: AsyncSession, now: datetime | None = None
) -> dict[int, tuple[Reservation, str]]:
    """Ближайшая бронь каждой машины с именем человека — для доски и бота."""
    now = now or _utcnow()
    rows = (
        await db.execute(
            select(Reservation, User.name)
            .join(User, User.id == Reservation.user_id)
            .where(
                Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
                Reservation.ends_at > now,
            )
            .order_by(Reservation.starts_at)
        )
    ).all()

    nearest: dict[int, tuple[Reservation, str]] = {}
    for reservation, name in rows:
        nearest.setdefault(reservation.machine_id, (reservation, name))
    return nearest


async def of_user(db: AsyncSession, user_id: int) -> list[tuple[Reservation, Machine]]:
    """Брони человека, ближайшая первой — экран «мои брони»."""
    rows = (
        await db.execute(
            select(Reservation, Machine)
            .join(Machine, Machine.id == Reservation.machine_id)
            .where(
                Reservation.user_id == user_id,
                Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
            )
            .order_by(Reservation.starts_at)
        )
    ).all()
    return [(reservation, machine) for reservation, machine in rows]


async def booked_ahead(db: AsyncSession, now: datetime | None = None) -> list[tuple]:
    """Все незакрытые брони с именами машин и людей — для админки."""
    now = now or _utcnow()
    return (
        await db.execute(
            select(Reservation, Machine.name, User.name)
            .join(Machine, Machine.id == Reservation.machine_id)
            .join(User, User.id == Reservation.user_id)
            .where(
                Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
                Reservation.ends_at > now,
            )
            .order_by(Reservation.starts_at)
        )
    ).all()


async def due_to_remind(db: AsyncSession, now: datetime) -> list[Reservation]:
    """Брони, о которых пора напомнить: старт ближе, чем через N минут."""
    threshold = now + timedelta(minutes=settings.reservation_remind_minutes)
    return list(
        (
            await db.scalars(
                select(Reservation).where(
                    Reservation.status == ReservationStatus.BOOKED,
                    Reservation.reminded_at.is_(None),
                    Reservation.starts_at <= threshold,
                    Reservation.starts_at > now,
                )
            )
        ).all()
    )


async def due_to_start(db: AsyncSession, now: datetime) -> list[Reservation]:
    """Брони, окно которых уже началось, а сообщение об этом ещё не ушло."""
    return list(
        (
            await db.scalars(
                select(Reservation).where(
                    Reservation.status == ReservationStatus.BOOKED,
                    Reservation.started_notified_at.is_(None),
                    Reservation.starts_at <= now,
                )
            )
        ).all()
    )


async def due_to_expire(db: AsyncSession, now: datetime) -> list[Reservation]:
    """Брони, чей grace истёк. Свободна ли машина, проверяет `expire_no_show`."""
    deadline = now - timedelta(minutes=settings.reservation_grace_minutes)
    return list(
        (
            await db.scalars(
                select(Reservation).where(
                    Reservation.status == ReservationStatus.BOOKED,
                    Reservation.starts_at <= deadline,
                )
            )
        ).all()
    )


async def day_schedule(
    db: AsyncSession,
    park: list[Machine],
    kind: str,
    day: date,
    now: datetime | None = None,
    viewer_id: int | None = None,
) -> DaySchedule:
    """Расписание суток: столбец на машину, строка на слот.

    Часы строками, а не столбцами: 24 столбца читаются на iPad и не читаются на
    телефоне, а экран один и тот же — и на стене, и в Mini App.

    Парк приходит аргументом, а не выбирается здесь: список машин умеет отдавать
    services/machines.py, и импортировать его сюда значило бы замкнуть кольцо
    зависимостей (см. преамбулу модуля).
    """
    now = now or _utcnow()
    start, end = schedule.day_bounds(day)

    reservations: dict[int, list[tuple[Reservation, str]]] = {}
    for reservation, name in (
        await db.execute(
            select(Reservation, User.name)
            .join(User, User.id == Reservation.user_id)
            .where(
                Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
                Reservation.starts_at < end,
                Reservation.ends_at > start,
            )
        )
    ).all():
        reservations.setdefault(reservation.machine_id, []).append((reservation, name))

    sessions: dict[int, list[tuple[MachineSession, str]]] = {}
    for session, name in (
        await db.execute(
            select(MachineSession, User.name)
            .join(User, User.id == MachineSession.user_id)
            .where(
                MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
                MachineSession.started_at < end,
            )
        )
    ).all():
        sessions.setdefault(session.machine_id, []).append((session, name))

    slots = schedule.slot_starts(day)
    step = timedelta(minutes=settings.reservation_slot_minutes)

    columns = []
    for machine in park:
        cells = [
            _cell(
                machine=machine,
                slot=slot,
                slot_end=slot + step,
                now=now,
                viewer_id=viewer_id,
                reservations=reservations.get(machine.id, []),
                sessions=sessions.get(machine.id, []),
            )
            for slot in slots
        ]
        columns.append(
            MachineColumn(machine_id=machine.id, name=machine.name, cells=_mark_runs(cells))
        )

    return DaySchedule(
        kind=kind,
        day=day,
        days=schedule.day_options(now),
        columns=columns,
        hours=[schedule.local(slot).strftime(t.TIME_FORMAT) for slot in slots],
    )


# --- внутреннее --------------------------------------------------------------


def _cell(
    machine: Machine,
    slot: datetime,
    slot_end: datetime,
    now: datetime,
    viewer_id: int | None,
    reservations: list[tuple[Reservation, str]],
    sessions: list[tuple[MachineSession, str]],
) -> Cell:
    label = schedule.local(slot).strftime(t.TIME_FORMAT)

    # Бронь показывается раньше поломки и раньше работы: человек, у которого
    # окно в этот час, должен видеть его своим, что бы ни случилось с машиной.
    booking = next(
        (
            row
            for row in reservations
            if row[0].starts_at < slot_end and row[0].ends_at > slot
        ),
        None,
    )
    if booking is not None:
        reservation, name = booking
        state = CELL_MINE if viewer_id and reservation.user_id == viewer_id else CELL_BOOKED
        return Cell(
            starts_at=slot,
            label=label,
            state=state,
            who=name,
            reservation_id=reservation.id,
        )

    if machine.status == MachineStatus.BROKEN:
        return Cell(starts_at=slot, label=label, state=CELL_BROKEN)

    working = next(
        (
            row
            for row in sessions
            if row[0].started_at < slot_end and _session_end(row[0], now) > slot
        ),
        None,
    )
    if working is not None:
        return Cell(starts_at=slot, label=label, state=CELL_BUSY, who=working[1])

    if slot_end <= now:
        return Cell(starts_at=slot, label=label, state=CELL_PAST)
    return Cell(starts_at=slot, label=label, state=CELL_FREE)


def _mark_runs(cells: list[Cell]) -> list[Cell]:
    """Пометить клетки, продолжающие занятие предыдущего часа."""
    marked = [cells[0]] if cells else []
    for previous, cell in zip(cells, cells[1:], strict=False):
        same = cell.state == previous.state and cell.who == previous.who
        marked.append(
            Cell(
                starts_at=cell.starts_at,
                label=cell.label,
                state=cell.state,
                who=cell.who,
                reservation_id=cell.reservation_id,
                continues=same and cell.state != CELL_FREE,
            )
        )
    return marked


def _session_end(session: MachineSession, now: datetime) -> datetime:
    """Докуда работа занимает машину в расписании.

    Деталь, лежащая на столе (`done_wait`), занимает его прямо сейчас, хотя
    расчётный конец уже позади: нарисовать этот слот свободным — значит позвать
    человека к занятому столу.
    """
    if session.status == SessionStatus.DONE_WAIT:
        return max(session.eta_at, now)
    return session.eta_at


async def _lock_machine(db: AsyncSession, machine_id: int) -> Machine:
    """Тот же замок на строке машины, что в services/machines.py.

    Двойник, а не импорт: см. преамбулу модуля. Точка сериализации у брони и у
    занятия должна быть одна и та же строка, иначе «забронировать» и «занять»
    разойдутся в гонке.
    """
    machine = await db.get(Machine, machine_id, with_for_update=True)
    if machine is None:
        raise MachineNotAvailable(t.ERR_MACHINE_NOT_FOUND)
    return machine


async def _ensure_window_free(
    db: AsyncSession, machine: Machine, starts_at: datetime, ends_at: datetime
) -> None:
    """Отказ с понятным текстом до того, как сработает ограничение БД."""
    clash = await db.scalar(
        select(Reservation)
        .where(
            Reservation.machine_id == machine.id,
            Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
            Reservation.starts_at < ends_at,
            Reservation.ends_at > starts_at,
        )
        .order_by(Reservation.starts_at)
        .limit(1)
    )
    if clash is not None:
        raise ReservationOverlap(
            t.ERR_RESERVATION_OVERLAP.format(machine=machine.name, time=_hhmm(clash.starts_at))
        )

    # Идущая работа тоже держит окно: расчётный конец — это всё, что мы про неё
    # знаем, и бронь поверх него позвала бы двоих к одному столу. Незабранная
    # деталь (`done_wait`) окно не держит — её снимет тот, кто придёт по брони
    # (правило 9).
    session = await db.scalar(
        select(MachineSession).where(
            MachineSession.machine_id == machine.id,
            MachineSession.status == SessionStatus.PRINTING,
            MachineSession.eta_at > starts_at,
        )
    )
    if session is not None:
        raise ReservationOverlap(
            t.ERR_RESERVATION_BUSY.format(machine=machine.name, time=_hhmm(session.eta_at))
        )


def _integrity_error(exc: IntegrityError, machine_name: str) -> Exception:
    """Какое из ограничений сработало — по имени в диагностике psycopg."""
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""
    if constraint == "one_active_reservation_per_user":
        return AlreadyBooked(t.ERR_ALREADY_BOOKED)
    return ReservationOverlap(t.ERR_RESERVATION_JUST_BOOKED.format(machine=machine_name))


def _hhmm(value: datetime) -> str:
    return schedule.local(value).strftime(t.TIME_FORMAT)
