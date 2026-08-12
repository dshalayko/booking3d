"""Очередь.

Ради неё система и существует: машины заняты работами по 4–12 часов, значит
большую часть времени свободных нет, и ценность в ответе на вопрос «когда
освободится и я ли следующий».

Правила отсюда (PLAN.md):
  3. очередь общая на все машины одного типа;
  4. предложение получает только первый, а не рассылка всем;
  5. окно подтверждения 30 минут;
  6. ночью окно не тикает.

Про типы. Очередей столько, сколько типов оборудования: ждущий гравировщик и
ждущий принтер стоят в разных списках. Один список на весь парк означал бы, что
освободившийся принтер предлагается человеку с файлом для гравировки — он
откажется, а машина простоит впустую всё окно подтверждения. Место в очереди
при этом по-прежнему одно на человека (правило 2): занять всё равно можно
только что-то одно.

Функции ничего не отправляют в Telegram: они возвращают описание того, что
произошло, а отправкой занимается вызывающий слой (шаги 6–7). Так домен
остаётся тестируемым без бота.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.config import settings
from app.enums import (
    ACTIVE_QUEUE_STATUSES,
    ACTIVE_RESERVATION_STATUSES,
    ACTIVE_SESSION_STATUSES,
    MachineKind,
    MachineStatus,
    QueueStatus,
)
from app.models import Machine, MachineSession, QueueEntry, Reservation
from app.services.errors import (
    AlreadyInQueue,
    MachineKindUnknown,
    NotInQueue,
    OfferNotActive,
    UserBusy,
)
from app.services.schedule import MIN_DURATION_MINUTES
from app.services.timeutil import add_active_minutes


@dataclass(frozen=True)
class Offer:
    """Предложение занять освободившуюся машину, ушедшее первому в очереди."""

    entry_id: int
    user_id: int
    machine_id: int
    machine_name: str
    kind: str
    expires_at: datetime


@dataclass(frozen=True)
class JoinResult:
    entry_id: int
    kind: str
    position: int
    offers: list[Offer]


@dataclass(frozen=True)
class LeaveResult:
    entry_id: int
    offers: list[Offer]


@dataclass(frozen=True)
class ExpireResult:
    entry_id: int
    user_id: int
    machine_id: int
    offers: list[Offer]


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def join(
    db: AsyncSession, user_id: int, kind: str, now: datetime | None = None
) -> JoinResult:
    """Встать в очередь на машины указанного типа.

    Человек с активной работой в очередь не встаёт: иначе один занимает машину
    и держит место на вторую, а при небольшом парке это его монополия.
    """
    if kind not in tuple(MachineKind):
        raise MachineKindUnknown(t.ERR_MACHINE_KIND_UNKNOWN.format(kind=kind))

    now = now or _utcnow()

    active_session = await db.scalar(
        select(MachineSession.id).where(
            MachineSession.user_id == user_id,
            MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )
    if active_session is not None:
        raise UserBusy(t.ERR_USER_BUSY_FREE_FIRST)

    existing = await _active_entry(db, user_id)
    if existing is not None:
        raise AlreadyInQueue(t.ERR_ALREADY_IN_QUEUE)

    entry = QueueEntry(user_id=user_id, kind=kind, status=QueueStatus.WAITING, created_at=now)
    db.add(entry)
    await db.flush()

    # Если прямо сейчас есть свободная машина этого типа, предложение уйдёт
    # сразу же.
    offers = await offer_free_machines(db, now)
    position = await position_of(db, user_id) or 1
    return JoinResult(entry_id=entry.id, kind=kind, position=position, offers=offers)


async def leave(db: AsyncSession, user_id: int, now: datetime | None = None) -> LeaveResult:
    """Выйти из очереди. Освободившееся предложение уходит следующему.

    Тип не спрашиваем: место в очереди у человека одно, и какое именно — видно
    по записи.
    """
    now = now or _utcnow()

    entry = await _active_entry(db, user_id)
    if entry is None:
        raise NotInQueue(t.ERR_NOT_IN_QUEUE)

    # `offered_machine_id` не затираем: активным предложение делает статус, а не
    # заполненное поле, зато в журнале останется, на какую машину приглашали.
    entry.status = QueueStatus.LEFT
    entry.resolved_at = now
    await db.flush()

    offers = await offer_free_machines(db, now)
    return LeaveResult(entry_id=entry.id, offers=offers)


async def offer_free_machines(db: AsyncSession, now: datetime | None = None) -> list[Offer]:
    """Раздать свободные машины первым в очереди их типа.

    Правило 4: на каждую свободную машину уходит ровно одно предложение первому
    ожидающему этот тип. Машина при этом остаётся в статусе `free` — она
    физически свободна, но занять её может только адресат предложения
    (правило 7).

    Забронированные машины не раздаются (правило 12): в чужое окно занять их всё
    равно нельзя, и предложение сгорело бы впустую, придержав машину на все 30
    минут. Так же пропускаются машины, у которых до брони осталось меньше
    минимальной работы, — предложение на десять минут не предложение.
    """
    now = now or _utcnow()

    reserved = select(QueueEntry.offered_machine_id).where(
        QueueEntry.status == QueueStatus.OFFERED,
        QueueEntry.offered_machine_id.is_not(None),
    )
    # Запрос по `Reservation` напрямую, а не через services/reservations.py: тот
    # импортирует эту функцию, и обратный импорт замкнул бы кольцо.
    booked = select(Reservation.machine_id).where(
        Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
        Reservation.starts_at <= now + timedelta(minutes=MIN_DURATION_MINUTES),
        Reservation.ends_at > now,
    )
    free_machines = (
        await db.scalars(
            select(Machine)
            .where(
                Machine.status == MachineStatus.FREE,
                Machine.id.not_in(reserved),
                Machine.id.not_in(booked),
            )
            .order_by(Machine.id)
            .with_for_update()
        )
    ).all()

    offers: list[Offer] = []
    for machine in free_machines:
        entry = await db.scalar(
            select(QueueEntry)
            .where(
                QueueEntry.status == QueueStatus.WAITING,
                QueueEntry.kind == machine.kind,
            )
            .order_by(QueueEntry.created_at, QueueEntry.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if entry is None:
            # Очередь этого типа пуста — машина просто стоит свободной.
            # Не `break`: у машин другого типа очередь может быть непустой.
            continue

        entry.status = QueueStatus.OFFERED
        entry.offered_machine_id = machine.id
        entry.offered_at = now
        # Правило 6: ночью окно не тикает, иначе предложение сгорит в 03:40
        # впустую и к утру очередь опустеет, никого не пустив.
        entry.offer_expires_at = add_active_minutes(
            now,
            settings.offer_window_minutes,
            settings.night_start,
            settings.night_end,
            tz=settings.zone,
        )
        await db.flush()

        offers.append(
            Offer(
                entry_id=entry.id,
                user_id=entry.user_id,
                machine_id=machine.id,
                machine_name=machine.name,
                kind=machine.kind,
                expires_at=entry.offer_expires_at,
            )
        )

    return offers


async def expire_offer(
    db: AsyncSession, entry_id: int, now: datetime | None = None
) -> ExpireResult:
    """Закрыть просроченное предложение и передать машину следующему."""
    now = now or _utcnow()

    entry = await db.get(QueueEntry, entry_id, with_for_update=True)
    if entry is None or entry.status != QueueStatus.OFFERED:
        raise OfferNotActive(t.ERR_OFFER_NOT_ACTIVE)
    if entry.offer_expires_at is not None and entry.offer_expires_at > now:
        raise OfferNotActive(t.ERR_OFFER_WINDOW_OPEN)

    machine_id = entry.offered_machine_id
    entry.status = QueueStatus.EXPIRED
    entry.resolved_at = now
    await db.flush()

    offers = await offer_free_machines(db, now)
    return ExpireResult(
        entry_id=entry.id,
        user_id=entry.user_id,
        machine_id=machine_id,
        offers=offers,
    )


async def active_entries(db: AsyncSession, kind: str | None = None) -> list[QueueEntry]:
    """Очередь в порядке FIFO — то, что видно на киоске.

    Без `kind` — все ожидания подряд, для журнала админки. С `kind` — та самая
    очередь, номера в которой человек видит на экране.
    """
    query = select(QueueEntry).where(QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES))
    if kind is not None:
        query = query.where(QueueEntry.kind == kind)
    return list(
        (await db.scalars(query.order_by(QueueEntry.created_at, QueueEntry.id))).all()
    )


async def position_of(db: AsyncSession, user_id: int) -> int | None:
    """Позиция человека в его очереди, считая с 1. None — если его там нет.

    Считается внутри своего типа: третий в очереди на гравировщик — третий, а
    не пятый из-за двоих, ждущих принтер.
    """
    entry = await _active_entry(db, user_id)
    if entry is None:
        return None
    for index, item in enumerate(await active_entries(db, kind=entry.kind), start=1):
        if item.user_id == user_id:
            return index
    return None


async def offer_for_machine(db: AsyncSession, machine_id: int) -> QueueEntry | None:
    """Активное предложение на эту машину, если оно есть."""
    return await db.scalar(
        select(QueueEntry).where(
            QueueEntry.status == QueueStatus.OFFERED,
            QueueEntry.offered_machine_id == machine_id,
        )
    )


async def has_active_entries(db: AsyncSession, kind: str | None = None) -> bool:
    query = select(QueueEntry.id).where(QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES))
    if kind is not None:
        query = query.where(QueueEntry.kind == kind)
    return await db.scalar(query.limit(1)) is not None


async def _active_entry(db: AsyncSession, user_id: int) -> QueueEntry | None:
    return await db.scalar(
        select(QueueEntry).where(
            QueueEntry.user_id == user_id,
            QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES),
        )
    )
