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
from app.enums import ACTIVE_SESSION_STATUSES, MachineStatus, QueueStatus, SessionStatus
from app.models import Machine, MachineSession, QueueEntry, User
from app.services import machines as machines_svc
from app.services import queue as queue_svc
from app.services import reservations as reservations_svc
from app.services.errors import DomainError

logger = logging.getLogger(__name__)


@dataclass
class Report:
    """Что сделала сверка. Нужен для логов и тестов."""

    warned: int = 0
    finished: int = 0
    unclaimed: int = 0
    expired_offers: int = 0
    bookings_reminded: int = 0
    bookings_started: int = 0
    bookings_expired: int = 0

    @property
    def touched(self) -> int:
        return (
            self.warned
            + self.finished
            + self.unclaimed
            + self.expired_offers
            + self.bookings_reminded
            + self.bookings_started
            + self.bookings_expired
        )


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
    # Брони идут после сессий: работа, только что перешедшая в «готово», должна
    # успеть освободить машину до того, как мы решим, ждёт ли бронь занятый стол.
    await _remind_bookings(db, now, report, pending)
    await _start_bookings(db, now, report, pending)
    await _expire_bookings(db, now, report, pending, offers)

    if report.touched:
        await db.commit()

    for message in pending.messages:
        await notify.send_to_user(db, message.user_id, message.text)
    await notify.announce_offers(db, offers)

    if report.touched:
        logger.info(
            "сверка: предупреждено %s, завершено %s, напоминаний о детали %s, "
            "просроченных предложений %s, брони: напомнили %s, начались %s, сняты %s",
            report.warned,
            report.finished,
            report.unclaimed,
            report.expired_offers,
            report.bookings_reminded,
            report.bookings_started,
            report.bookings_expired,
        )
    return report


async def _warn_before_finish(
    db: AsyncSession, now: datetime, report: Report, pending: _Pending
) -> None:
    """За 15 минут до расчётного конца — владельцу работы."""
    threshold = now + timedelta(minutes=settings.warn_before_minutes)

    sessions = (
        await db.scalars(
            select(MachineSession).where(
                MachineSession.status == SessionStatus.PRINTING,
                MachineSession.warned_at.is_(None),
                MachineSession.eta_at <= threshold,
                # Если срок уже прошёл, предупреждать поздно: человеку уйдёт
                # сообщение о том, что работа закончилась.
                MachineSession.eta_at > now,
            )
        )
    ).all()

    for session in sessions:
        machine = await db.get(Machine, session.machine_id)
        minutes = max(1, round((session.eta_at - now).total_seconds() / 60))
        session.warned_at = now
        pending.add(
            session.user_id, texts.almost_done(machine.name, machine.kind, minutes)
        )
        report.warned += 1


async def _finish_overdue(
    db: AsyncSession, now: datetime, report: Report, pending: _Pending
) -> None:
    """Правило 8: срок вышел — машина уходит в «готово», но не в «свободна»."""
    sessions = (
        await db.scalars(
            select(MachineSession).where(
                MachineSession.status == SessionStatus.PRINTING,
                MachineSession.eta_at <= now,
            )
        )
    ).all()

    for session in sessions:
        try:
            result = await machines_svc.mark_done_wait(db, session.machine_id, now=now)
        except DomainError as error:
            # Машину успели освободить или сломать между выборкой и переходом.
            logger.info("пропускаю завершение сессии %s: %s", session.id, error)
            continue

        session.finished_notified_at = now
        pending.add(
            result.owner_user_id,
            texts.finished(result.machine_name, result.machine_kind),
        )

        owner_name = await db.scalar(select(User.name).where(User.id == result.owner_user_id))
        # Подсказка «сходи проверь» уходит первому в этой очереди: тому, кто ждёт
        # гравировщик — или принтер в другом помещении, — этот принтер не нужен.
        first = await _first_in_queue(db, result.room_id, result.machine_kind)
        if first is not None:
            pending.add(
                first.user_id,
                texts.check_machine(
                    result.machine_name, result.machine_kind, owner_name or ""
                ),
            )
        report.finished += 1


async def _ping_unclaimed(
    db: AsyncSession, now: datetime, report: Report, pending: _Pending
) -> None:
    """Незабранная деталь — главная причина простоя небольшого парка."""
    deadline = now - timedelta(minutes=settings.unclaimed_ping_minutes)

    sessions = (
        await db.scalars(
            select(MachineSession).where(
                MachineSession.status == SessionStatus.DONE_WAIT,
                MachineSession.unclaimed_notified_at.is_(None),
                MachineSession.eta_at <= deadline,
            )
        )
    ).all()

    for session in sessions:
        machine = await db.get(Machine, session.machine_id)
        minutes = round((now - session.eta_at).total_seconds() / 60)
        session.unclaimed_notified_at = now
        pending.add(
            session.user_id,
            texts.unclaimed_owner(machine.name, machine.kind, minutes),
        )

        owner_name = await db.scalar(select(User.name).where(User.id == session.user_id))
        first = await _first_in_queue(db, machine.room_id, machine.kind)
        if first is not None:
            pending.add(
                first.user_id,
                texts.unclaimed_queue(machine.name, machine.kind, owner_name or "", minutes),
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
        machine_name = await db.scalar(
            select(Machine.name).where(Machine.id == entry.offered_machine_id)
        )
        try:
            result = await queue_svc.expire_offer(db, entry.id, now=now)
        except DomainError as error:
            logger.info("пропускаю истечение предложения %s: %s", entry.id, error)
            continue

        pending.add(result.user_id, texts.offer_expired(machine_name or t.BOT_MACHINE_FALLBACK))
        offers.extend(result.offers)
        report.expired_offers += 1


async def _remind_bookings(
    db: AsyncSession, now: datetime, report: Report, pending: _Pending
) -> None:
    """За час до брони — тому, кто её взял, и тому, кто сейчас на машине.

    Второе сообщение важнее первого: человек с активной работой не обязан
    помнить чужое расписание, а деталь, оставленная на столе, — главная причина,
    по которой бронь начинается с пустого ожидания.
    """
    for reservation in await reservations_svc.due_to_remind(db, now):
        machine = await db.get(Machine, reservation.machine_id)
        if machine is None:
            continue

        reservation.reminded_at = now
        minutes = max(1, round((reservation.starts_at - now).total_seconds() / 60))
        pending.add(
            reservation.user_id,
            texts.booking_soon(machine.name, reservation.starts_at, minutes),
        )

        session = await _active_session(db, machine.id)
        if session is not None and session.user_id != reservation.user_id:
            pending.add(
                session.user_id,
                texts.booking_after_you(machine.name, reservation.starts_at),
            )
        report.bookings_reminded += 1


async def _start_bookings(
    db: AsyncSession, now: datetime, report: Report, pending: _Pending
) -> None:
    """Окно началось: сказать, свободна машина или на столе чужая деталь."""
    for reservation in await reservations_svc.due_to_start(db, now):
        machine = await db.get(Machine, reservation.machine_id)
        if machine is None:
            continue

        reservation.started_notified_at = now
        if machine.status == MachineStatus.FREE:
            deadline = reservation.starts_at + timedelta(
                minutes=settings.reservation_grace_minutes
            )
            pending.add(reservation.user_id, texts.booking_started(machine.name, deadline))
        else:
            # Правило 14: пока стол занят, бронь не сгорает, поэтому и срока в
            # сообщении нет — есть только то, что человек может сделать.
            pending.add(reservation.user_id, texts.booking_started_busy(machine.name))
        report.bookings_started += 1


async def _expire_bookings(
    db: AsyncSession,
    now: datetime,
    report: Report,
    pending: _Pending,
    offers: list[queue_svc.Offer],
) -> None:
    """Не пришёл за отведённые минуты — бронь снимается, машина уходит очереди.

    Занятую машину `expire_no_show` не тронет (правило 14) и ответит отказом;
    такая бронь просто дождётся следующей сверки.
    """
    for reservation in await reservations_svc.due_to_expire(db, now):
        try:
            result = await reservations_svc.expire_no_show(db, reservation.id, now=now)
        except DomainError as error:
            logger.info("бронь %s пока не снимаю: %s", reservation.id, error)
            continue

        pending.add(
            result.user_id,
            texts.booking_missed(
                result.machine_name, settings.reservation_grace_minutes
            ),
        )
        offers.extend(result.offers)
        report.bookings_expired += 1


async def _active_session(db: AsyncSession, machine_id: int) -> MachineSession | None:
    return await db.scalar(
        select(MachineSession).where(
            MachineSession.machine_id == machine_id,
            MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )


async def _first_in_queue(db: AsyncSession, room_id: int, kind: str) -> QueueEntry | None:
    entries = await queue_svc.active_entries(db, room_id=room_id, kind=kind)
    return entries[0] if entries else None


async def has_running_now(db: AsyncSession) -> bool:
    """Есть ли вообще активные работы — для диагностики."""
    found = await db.scalar(
        select(Machine.id).where(Machine.status == MachineStatus.PRINTING).limit(1)
    )
    return found is not None
