"""Очередь.

Ради неё система и существует: два принтера с печатями по 4–12 часов означают,
что большую часть времени оба заняты, и ценность в ответе на вопрос «когда
освободится и я ли следующий».

Правила отсюда (PLAN.md):
  3. очередь общая на оба принтера;
  4. предложение получает только первый, а не рассылка всем;
  5. окно подтверждения 30 минут;
  6. ночью окно не тикает.

Функции ничего не отправляют в Telegram: они возвращают описание того, что
произошло, а отправкой занимается вызывающий слой (шаги 6–7). Так домен
остаётся тестируемым без бота.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.config import settings
from app.enums import ACTIVE_QUEUE_STATUSES, ACTIVE_SESSION_STATUSES, PrinterStatus, QueueStatus
from app.models import Printer, PrintSession, QueueEntry
from app.services.errors import AlreadyInQueue, NotInQueue, OfferNotActive, UserBusy
from app.services.timeutil import add_active_minutes


@dataclass(frozen=True)
class Offer:
    """Предложение занять освободившийся принтер, ушедшее первому в очереди."""

    entry_id: int
    user_id: int
    printer_id: int
    printer_name: str
    expires_at: datetime


@dataclass(frozen=True)
class JoinResult:
    entry_id: int
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
    printer_id: int
    offers: list[Offer]


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def join(db: AsyncSession, user_id: int, now: datetime | None = None) -> JoinResult:
    """Встать в очередь.

    Человек с активной печатью в очередь не встаёт: иначе один занимает принтер
    и держит место на второй, а при парке из двух машин это его монополия.
    """
    now = now or _utcnow()

    active_session = await db.scalar(
        select(PrintSession.id).where(
            PrintSession.user_id == user_id,
            PrintSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )
    if active_session is not None:
        raise UserBusy(t.ERR_USER_BUSY_FREE_FIRST)

    existing = await _active_entry(db, user_id)
    if existing is not None:
        raise AlreadyInQueue(t.ERR_ALREADY_IN_QUEUE)

    entry = QueueEntry(user_id=user_id, status=QueueStatus.WAITING, created_at=now)
    db.add(entry)
    await db.flush()

    # Если прямо сейчас есть свободный принтер, предложение уйдёт сразу же.
    offers = await offer_free_printers(db, now)
    position = await position_of(db, user_id) or 1
    return JoinResult(entry_id=entry.id, position=position, offers=offers)


async def leave(db: AsyncSession, user_id: int, now: datetime | None = None) -> LeaveResult:
    """Выйти из очереди. Освободившееся предложение уходит следующему."""
    now = now or _utcnow()

    entry = await _active_entry(db, user_id)
    if entry is None:
        raise NotInQueue(t.ERR_NOT_IN_QUEUE)

    # `offered_printer_id` не затираем: активным предложение делает статус, а не
    # заполненное поле, зато в журнале останется, на какой принтер приглашали.
    entry.status = QueueStatus.LEFT
    entry.resolved_at = now
    await db.flush()

    offers = await offer_free_printers(db, now)
    return LeaveResult(entry_id=entry.id, offers=offers)


async def offer_free_printers(db: AsyncSession, now: datetime | None = None) -> list[Offer]:
    """Раздать свободные принтеры первым в очереди.

    Правило 4: на каждый свободный принтер уходит ровно одно предложение
    первому ожидающему. Принтер при этом остаётся в статусе `free` — он
    физически свободен, но занять его может только адресат предложения
    (правило 7).
    """
    now = now or _utcnow()

    reserved = select(QueueEntry.offered_printer_id).where(
        QueueEntry.status == QueueStatus.OFFERED,
        QueueEntry.offered_printer_id.is_not(None),
    )
    free_printers = (
        await db.scalars(
            select(Printer)
            .where(Printer.status == PrinterStatus.FREE, Printer.id.not_in(reserved))
            .order_by(Printer.id)
            .with_for_update()
        )
    ).all()

    offers: list[Offer] = []
    for printer in free_printers:
        entry = await db.scalar(
            select(QueueEntry)
            .where(QueueEntry.status == QueueStatus.WAITING)
            .order_by(QueueEntry.created_at, QueueEntry.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if entry is None:
            break  # очередь пуста, остальные принтеры просто стоят свободными

        entry.status = QueueStatus.OFFERED
        entry.offered_printer_id = printer.id
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
                printer_id=printer.id,
                printer_name=printer.name,
                expires_at=entry.offer_expires_at,
            )
        )

    return offers


async def expire_offer(
    db: AsyncSession, entry_id: int, now: datetime | None = None
) -> ExpireResult:
    """Закрыть просроченное предложение и передать принтер следующему."""
    now = now or _utcnow()

    entry = await db.get(QueueEntry, entry_id, with_for_update=True)
    if entry is None or entry.status != QueueStatus.OFFERED:
        raise OfferNotActive(t.ERR_OFFER_NOT_ACTIVE)
    if entry.offer_expires_at is not None and entry.offer_expires_at > now:
        raise OfferNotActive(t.ERR_OFFER_WINDOW_OPEN)

    printer_id = entry.offered_printer_id
    entry.status = QueueStatus.EXPIRED
    entry.resolved_at = now
    await db.flush()

    offers = await offer_free_printers(db, now)
    return ExpireResult(
        entry_id=entry.id,
        user_id=entry.user_id,
        printer_id=printer_id,
        offers=offers,
    )


async def active_entries(db: AsyncSession) -> list[QueueEntry]:
    """Очередь в порядке FIFO — то, что видно на киоске."""
    return list(
        (
            await db.scalars(
                select(QueueEntry)
                .where(QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES))
                .order_by(QueueEntry.created_at, QueueEntry.id)
            )
        ).all()
    )


async def position_of(db: AsyncSession, user_id: int) -> int | None:
    """Позиция человека в очереди, считая с 1. None — если его там нет."""
    for index, entry in enumerate(await active_entries(db), start=1):
        if entry.user_id == user_id:
            return index
    return None


async def offer_for_printer(db: AsyncSession, printer_id: int) -> QueueEntry | None:
    """Активное предложение на этот принтер, если оно есть."""
    return await db.scalar(
        select(QueueEntry).where(
            QueueEntry.status == QueueStatus.OFFERED,
            QueueEntry.offered_printer_id == printer_id,
        )
    )


async def has_active_entries(db: AsyncSession) -> bool:
    entry_id = await db.scalar(
        select(QueueEntry.id).where(QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES)).limit(1)
    )
    return entry_id is not None


async def _active_entry(db: AsyncSession, user_id: int) -> QueueEntry | None:
    return await db.scalar(
        select(QueueEntry).where(
            QueueEntry.user_id == user_id,
            QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES),
        )
    )
