"""Напоминания и переходы по времени.

**Почему сверка, а не запланированные задания.**

В плане стоял APScheduler с заданиями в БД: при занятии принтера кладём в
хранилище задачу «за 15 минут до конца», при выдаче предложения — «истечение
окна». Такой подход опирается на то, что задание доживёт до срока. А теряется
оно легко: восстановили базу из бэкапа, переименовали функцию (APScheduler
хранит путь к ней строкой), сменили часовой пояс. Потерянное задание — это
принтер, навсегда застрявший в статусе «печатает».

Здесь вместо этого одна функция, которая раз в минуту сверяет состояние в БД с
часами и доводит его до правильного. Она идемпотентна, переживает любой простой
(после запуска просто догоняет), и восстанавливать после рестарта нечего —
состояние и так в таблицах. Планировщик нужен только чтобы вызывать её по
таймеру, хранилище заданий не требуется.

Цена решения — отметки об отправке (`warned_at` и соседние), чтобы одно и то же
напоминание не ушло дважды. Они же служат журналом.

Порядок такой: сначала меняем состояние и коммитим, потом рассылаем. Если
процесс упадёт между коммитом и отправкой, человек не получит одно сообщение —
это лучше, чем получить его пять раз подряд.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.bot import notify, texts
from app.config import settings
from app.enums import PrinterStatus, QueueStatus, SessionStatus
from app.models import Printer, PrintSession, QueueEntry, User
from app.services import printers as printers_svc
from app.services import queue as queue_svc
from app.services.errors import DomainError

logger = logging.getLogger(__name__)


@dataclass
class Report:
    """Что сделала сверка. Нужен для логов и тестов."""

    warned: int = 0
    finished: int = 0
    unclaimed: int = 0
    expired_offers: int = 0

    @property
    def touched(self) -> int:
        return self.warned + self.finished + self.unclaimed + self.expired_offers


@dataclass
class _Message:
    user_id: int
    text: str


@dataclass
class _Pending:
    """Сообщения, накопленные до коммита."""

    messages: list[_Message] = field(default_factory=list)

    def add(self, user_id: int | None, text: str) -> None:
        if user_id is not None:
            self.messages.append(_Message(user_id, text))


async def reconcile(db: AsyncSession, now: datetime | None = None) -> Report:
    """Догнать состояние до текущего времени и разослать напоминания."""
    now = now or datetime.now(UTC)
    report = Report()
    pending = _Pending()
    offers: list[queue_svc.Offer] = []

    await _warn_before_finish(db, now, report, pending)
    await _finish_overdue(db, now, report, pending)
    await _ping_unclaimed(db, now, report, pending)
    await _expire_offers(db, now, report, pending, offers)

    if report.touched:
        await db.commit()

    for message in pending.messages:
        await notify.send_to_user(db, message.user_id, message.text)
    await notify.announce_offers(db, offers)

    if report.touched:
        logger.info(
            "сверка: предупреждено %s, завершено %s, напоминаний о детали %s, "
            "просроченных предложений %s",
            report.warned,
            report.finished,
            report.unclaimed,
            report.expired_offers,
        )
    return report


async def _warn_before_finish(
    db: AsyncSession, now: datetime, report: Report, pending: _Pending
) -> None:
    """За 15 минут до расчётного конца — владельцу печати."""
    threshold = now + timedelta(minutes=settings.warn_before_minutes)

    sessions = (
        await db.scalars(
            select(PrintSession).where(
                PrintSession.status == SessionStatus.PRINTING,
                PrintSession.warned_at.is_(None),
                PrintSession.eta_at <= threshold,
                # Если срок уже прошёл, предупреждать поздно: человеку уйдёт
                # сообщение о том, что печать закончилась.
                PrintSession.eta_at > now,
            )
        )
    ).all()

    for session in sessions:
        printer = await db.get(Printer, session.printer_id)
        minutes = max(1, round((session.eta_at - now).total_seconds() / 60))
        session.warned_at = now
        pending.add(session.user_id, texts.almost_done(printer.name, minutes))
        report.warned += 1


async def _finish_overdue(
    db: AsyncSession, now: datetime, report: Report, pending: _Pending
) -> None:
    """Правило 8: срок вышел — принтер уходит в «готово», но не в «свободен»."""
    sessions = (
        await db.scalars(
            select(PrintSession).where(
                PrintSession.status == SessionStatus.PRINTING,
                PrintSession.eta_at <= now,
            )
        )
    ).all()

    for session in sessions:
        try:
            result = await printers_svc.mark_done_wait(db, session.printer_id, now=now)
        except DomainError as error:
            # Принтер успели освободить или сломать между выборкой и переходом.
            logger.info("пропускаю завершение сессии %s: %s", session.id, error)
            continue

        session.finished_notified_at = now
        pending.add(result.owner_user_id, texts.finished(result.printer_name))

        owner_name = await db.scalar(select(User.name).where(User.id == result.owner_user_id))
        first = await _first_in_queue(db)
        if first is not None:
            pending.add(first.user_id, texts.check_printer(result.printer_name, owner_name or ""))
        report.finished += 1


async def _ping_unclaimed(
    db: AsyncSession, now: datetime, report: Report, pending: _Pending
) -> None:
    """Незабранная деталь — главная причина простоя парка из двух машин."""
    deadline = now - timedelta(minutes=settings.unclaimed_ping_minutes)

    sessions = (
        await db.scalars(
            select(PrintSession).where(
                PrintSession.status == SessionStatus.DONE_WAIT,
                PrintSession.unclaimed_notified_at.is_(None),
                PrintSession.eta_at <= deadline,
            )
        )
    ).all()

    for session in sessions:
        printer = await db.get(Printer, session.printer_id)
        minutes = round((now - session.eta_at).total_seconds() / 60)
        session.unclaimed_notified_at = now
        pending.add(session.user_id, texts.unclaimed_owner(printer.name, minutes))

        owner_name = await db.scalar(select(User.name).where(User.id == session.user_id))
        first = await _first_in_queue(db)
        if first is not None:
            pending.add(
                first.user_id,
                texts.unclaimed_queue(printer.name, owner_name or "", minutes),
            )
        report.unclaimed += 1


async def _expire_offers(
    db: AsyncSession,
    now: datetime,
    report: Report,
    pending: _Pending,
    offers: list[queue_svc.Offer],
) -> None:
    """Правило 5: не подтвердил за 30 минут — предложение уходит следующему.

    Ночная пауза уже учтена в `offer_expires_at` при выдаче предложения, здесь
    достаточно сравнить с часами.
    """
    entries = (
        await db.scalars(
            select(QueueEntry).where(
                QueueEntry.status == QueueStatus.OFFERED,
                QueueEntry.offer_expires_at <= now,
            )
        )
    ).all()

    for entry in entries:
        printer_name = await db.scalar(
            select(Printer.name).where(Printer.id == entry.offered_printer_id)
        )
        try:
            result = await queue_svc.expire_offer(db, entry.id, now=now)
        except DomainError as error:
            logger.info("пропускаю истечение предложения %s: %s", entry.id, error)
            continue

        pending.add(result.user_id, texts.offer_expired(printer_name or t.BOT_PRINTER_FALLBACK))
        offers.extend(result.offers)
        report.expired_offers += 1


async def _first_in_queue(db: AsyncSession) -> QueueEntry | None:
    entries = await queue_svc.active_entries(db)
    return entries[0] if entries else None


async def has_printing_now(db: AsyncSession) -> bool:
    """Есть ли вообще активные печати — для диагностики."""
    found = await db.scalar(
        select(Printer.id).where(Printer.status == PrinterStatus.PRINTING).limit(1)
    )
    return found is not None
