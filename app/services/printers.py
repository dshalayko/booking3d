"""Занятие и освобождение принтеров.

Правила отсюда (PLAN.md):
  1. одна активная сессия на принтер;
  2. одна активная сессия на человека;
  7. пока очередь непуста, занять свободный принтер может только адресат
     предложения (и админ);
  8. таймер не освобождает принтер автоматически — по истечении `eta_at`
     принтер уходит в `done_wait`, а не в `free`;
  9. освободить принтер может любой авторизованный, не только владелец печати.

Функции не коммитят: транзакцией управляет вызывающий слой. Уведомления тоже
не отправляют — возвращают описание случившегося.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.enums import ACTIVE_SESSION_STATUSES, PrinterStatus, QueueStatus, SessionStatus
from app.models import Printer, PrintSession, User
from app.services import queue
from app.services.errors import (
    InvalidDuration,
    NotAdmin,
    PrinterNotAvailable,
    PrinterReserved,
    UserBusy,
)

MIN_DURATION_MINUTES = 15
MAX_DURATION_MINUTES = 48 * 60


@dataclass(frozen=True)
class OccupyResult:
    session_id: int
    printer_id: int
    printer_name: str
    eta_at: datetime
    from_offer: bool


@dataclass(frozen=True)
class ReleaseResult:
    printer_id: int
    printer_name: str
    session_id: int | None
    owner_user_id: int | None
    session_status: str | None
    offers: list[queue.Offer] = field(default_factory=list)


@dataclass(frozen=True)
class DoneWaitResult:
    printer_id: int
    printer_name: str
    session_id: int
    owner_user_id: int


@dataclass(frozen=True)
class BrokenResult:
    printer_id: int
    printer_name: str
    cancelled_session_id: int | None
    owner_user_id: int | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def occupy(
    db: AsyncSession,
    user: User,
    printer_id: int,
    duration_minutes: int,
    now: datetime | None = None,
) -> OccupyResult:
    """Занять принтер сейчас на указанную длительность."""
    if not MIN_DURATION_MINUTES <= duration_minutes <= MAX_DURATION_MINUTES:
        raise InvalidDuration(
            t.ERR_DURATION.format(
                min_minutes=MIN_DURATION_MINUTES, max_hours=MAX_DURATION_MINUTES // 60
            )
        )

    now = now or _utcnow()
    printer = await _lock_printer(db, printer_id)

    if printer.status == PrinterStatus.BROKEN:
        raise PrinterNotAvailable(t.ERR_PRINTER_BROKEN.format(printer=printer.name))
    if printer.status != PrinterStatus.FREE:
        raise PrinterNotAvailable(t.ERR_PRINTER_BUSY.format(printer=printer.name))

    offer = await _check_queue_allows(db, user, printer)

    if await _active_session_of_user(db, user.id) is not None:
        raise UserBusy(t.ERR_USER_BUSY)

    # eta_at считается обычным сложением: печать идёт и ночью, ночная пауза
    # относится только к окну подтверждения предложения из очереди.
    session = PrintSession(
        printer_id=printer.id,
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
        raise PrinterNotAvailable(t.ERR_PRINTER_JUST_TAKEN.format(printer=printer.name)) from exc

    printer.status = PrinterStatus.PRINTING

    if offer is not None:
        offer.status = QueueStatus.TAKEN
        offer.resolved_at = now

    await db.flush()
    return OccupyResult(
        session_id=session.id,
        printer_id=printer.id,
        printer_name=printer.name,
        eta_at=session.eta_at,
        from_offer=offer is not None,
    )


async def release(
    db: AsyncSession,
    actor: User,
    printer_id: int,
    now: datetime | None = None,
    reason: str | None = None,
) -> ReleaseResult:
    """Освободить принтер: снятая деталь или прерванная печать.

    Правило 9: жать может любой авторизованный человек, не только владелец.
    Стол пустой — значит принтер свободен, и парк не должен простаивать из-за
    того, что владелец уехал домой. Кто освободил, пишется в `freed_by_user_id`.

    Из `done_wait` сессия закрывается как `completed` (печать состоялась), из
    `printing` — как `cancelled` (сняли на середине).
    """
    now = now or _utcnow()
    printer = await _lock_printer(db, printer_id)

    if printer.status == PrinterStatus.BROKEN:
        raise PrinterNotAvailable(t.ERR_PRINTER_BROKEN.format(printer=printer.name))

    session = await _active_session_of_printer(db, printer.id)
    session_id: int | None = None
    owner_user_id: int | None = None
    session_status: str | None = None

    if session is not None:
        session_status = (
            SessionStatus.COMPLETED
            if printer.status == PrinterStatus.DONE_WAIT
            else SessionStatus.CANCELLED
        )
        session.status = session_status
        session.ended_at = now
        session.freed_by_user_id = actor.id
        session.cancel_reason = reason
        session_id = session.id
        owner_user_id = session.user_id

    printer.status = PrinterStatus.FREE
    await db.flush()

    offers = await queue.offer_free_printers(db, now)
    return ReleaseResult(
        printer_id=printer.id,
        printer_name=printer.name,
        session_id=session_id,
        owner_user_id=owner_user_id,
        session_status=session_status,
        offers=offers,
    )


async def mark_done_wait(
    db: AsyncSession, printer_id: int, now: datetime | None = None
) -> DoneWaitResult:
    """Истёк `eta_at`: печать считается законченной, деталь ещё на столе.

    Правило 8: автоматически освобождать принтер по таймеру нельзя — оценка
    длительности всегда врёт, и освобождение вслепую приведёт к двум людям,
    печатающим на один стол.
    """
    now = now or _utcnow()
    printer = await _lock_printer(db, printer_id)

    if printer.status != PrinterStatus.PRINTING:
        raise PrinterNotAvailable(t.ERR_PRINTER_NOT_PRINTING.format(printer=printer.name))

    session = await _active_session_of_printer(db, printer.id)
    if session is None:
        raise PrinterNotAvailable(t.ERR_PRINTER_NO_SESSION.format(printer=printer.name))

    session.status = SessionStatus.DONE_WAIT
    printer.status = PrinterStatus.DONE_WAIT
    await db.flush()

    return DoneWaitResult(
        printer_id=printer.id,
        printer_name=printer.name,
        session_id=session.id,
        owner_user_id=session.user_id,
    )


async def set_broken(
    db: AsyncSession,
    admin: User,
    printer_id: int,
    note: str | None = None,
    now: datetime | None = None,
) -> BrokenResult:
    """Вывести принтер из строя. Активная печать снимается."""
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    now = now or _utcnow()
    printer = await _lock_printer(db, printer_id)

    session = await _active_session_of_printer(db, printer.id)
    cancelled_id: int | None = None
    owner_user_id: int | None = None
    if session is not None:
        session.status = SessionStatus.CANCELLED
        session.ended_at = now
        session.freed_by_user_id = admin.id
        session.cancel_reason = note or t.REASON_PRINTER_BROKEN
        cancelled_id = session.id
        owner_user_id = session.user_id

    printer.status = PrinterStatus.BROKEN
    printer.note = note
    await db.flush()

    return BrokenResult(
        printer_id=printer.id,
        printer_name=printer.name,
        cancelled_session_id=cancelled_id,
        owner_user_id=owner_user_id,
    )


async def clear_broken(
    db: AsyncSession, admin: User, printer_id: int, now: datetime | None = None
) -> ReleaseResult:
    """Вернуть принтер в строй и сразу предложить его очереди."""
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    now = now or _utcnow()
    printer = await _lock_printer(db, printer_id)

    if printer.status != PrinterStatus.BROKEN:
        raise PrinterNotAvailable(t.ERR_PRINTER_NOT_BROKEN.format(printer=printer.name))

    printer.status = PrinterStatus.FREE
    printer.note = None
    await db.flush()

    offers = await queue.offer_free_printers(db, now)
    return ReleaseResult(
        printer_id=printer.id,
        printer_name=printer.name,
        session_id=None,
        owner_user_id=None,
        session_status=None,
        offers=offers,
    )


async def list_printers(db: AsyncSession) -> list[Printer]:
    return list((await db.scalars(select(Printer).order_by(Printer.id))).all())


async def _check_queue_allows(db: AsyncSession, user: User, printer: Printer):
    """Правило 7: очередь имеет смысл, только если её нельзя обойти.

    Подошедший к киоску всегда быстрее того, кто едет из дома, поэтому пока
    очередь непуста, свободный принтер занимает только адресат предложения.
    Возвращает предложение этого человека, если оно есть.
    """
    offer = await queue.offer_for_printer(db, printer.id)
    if offer is not None:
        if offer.user_id == user.id:
            return offer
        if user.is_admin:
            return None
        raise PrinterReserved(t.ERR_PRINTER_RESERVED.format(printer=printer.name))

    if await queue.has_active_entries(db) and not user.is_admin:
        raise PrinterReserved(t.ERR_QUEUE_WAIT_YOUR_TURN)

    return None


async def _lock_printer(db: AsyncSession, printer_id: int) -> Printer:
    printer = await db.get(Printer, printer_id, with_for_update=True)
    if printer is None:
        raise PrinterNotAvailable(t.ERR_PRINTER_NOT_FOUND)
    return printer


async def _active_session_of_printer(db: AsyncSession, printer_id: int) -> PrintSession | None:
    return await db.scalar(
        select(PrintSession).where(
            PrintSession.printer_id == printer_id,
            PrintSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )


async def _active_session_of_user(db: AsyncSession, user_id: int) -> PrintSession | None:
    return await db.scalar(
        select(PrintSession).where(
            PrintSession.user_id == user_id,
            PrintSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )
