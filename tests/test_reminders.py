from datetime import UTC, datetime, timedelta

import pytest

from app.bot import notify
from app.enums import PrinterStatus, QueueStatus, SessionStatus
from app.models import Printer, PrintSession, QueueEntry
from app.services import printers as printers_svc
from app.services import queue as queue_svc
from app.services import reminders

NOON = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def outbox() -> list[tuple[int, str]]:
    sent: list[tuple[int, str]] = []

    async def sender(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    notify.set_sender(sender)
    yield sent
    notify.set_sender(None)


def texts_of(outbox) -> str:
    return "\n".join(text for _, text in outbox)


class TestWarnBeforeFinish:
    async def test_warns_fifteen_minutes_before(self, db, printers, make_user, outbox):
        user = await make_user()
        await printers_svc.occupy(db, user, printers[0].id, 60, now=NOON)
        await db.commit()

        report = await reminders.reconcile(db, now=NOON + timedelta(minutes=46))

        assert report.warned == 1
        assert len(outbox) == 1
        assert outbox[0][0] == user.tg_chat_id
        assert "заканчивается" in outbox[0][1]

    async def test_does_not_warn_too_early(self, db, printers, make_user, outbox):
        await printers_svc.occupy(db, await make_user(), printers[0].id, 60, now=NOON)
        await db.commit()

        report = await reminders.reconcile(db, now=NOON + timedelta(minutes=30))

        assert report.warned == 0
        assert outbox == []

    async def test_warns_only_once(self, db, printers, make_user, outbox):
        await printers_svc.occupy(db, await make_user(), printers[0].id, 60, now=NOON)
        await db.commit()
        moment = NOON + timedelta(minutes=50)

        first = await reminders.reconcile(db, now=moment)
        second = await reminders.reconcile(db, now=moment + timedelta(minutes=1))

        assert first.warned == 1
        assert second.warned == 0

    async def test_no_late_warning_after_deadline(self, db, printers, make_user, outbox):
        """Простой был долгим: предупреждать «скоро закончится» уже поздно."""
        await printers_svc.occupy(db, await make_user(), printers[0].id, 60, now=NOON)
        await db.commit()

        report = await reminders.reconcile(db, now=NOON + timedelta(hours=5))

        assert report.warned == 0
        assert report.finished == 1
        assert "заканчивается" not in texts_of(outbox)


class TestFinish:
    async def test_moves_printer_to_done_wait(self, db, printers, make_user, outbox):
        printer_id = printers[0].id
        user = await make_user()
        await printers_svc.occupy(db, user, printer_id, 60, now=NOON)
        await db.commit()

        report = await reminders.reconcile(db, now=NOON + timedelta(minutes=61))

        assert report.finished == 1
        db.expire_all()
        assert (await db.get(Printer, printer_id)).status == PrinterStatus.DONE_WAIT
        assert "заберите деталь" in texts_of(outbox).lower()

    async def test_tells_first_in_queue_to_check(self, db, printers, make_user, outbox):
        owner = await make_user(name="Иван")
        other = await make_user()
        waiting = await make_user()
        await printers_svc.occupy(db, owner, printers[0].id, 60, now=NOON)
        await printers_svc.occupy(db, other, printers[1].id, 600, now=NOON)
        await queue_svc.join(db, waiting.id, now=NOON)
        await db.commit()
        outbox.clear()

        await reminders.reconcile(db, now=NOON + timedelta(minutes=61))

        addressed = [text for chat, text in outbox if chat == waiting.tg_chat_id]
        assert len(addressed) == 1
        assert "должна была закончиться" in addressed[0]
        assert "Иван" in addressed[0]

    async def test_does_not_finish_twice(self, db, printers, make_user, outbox):
        await printers_svc.occupy(db, await make_user(), printers[0].id, 60, now=NOON)
        await db.commit()
        moment = NOON + timedelta(minutes=61)

        first = await reminders.reconcile(db, now=moment)
        second = await reminders.reconcile(db, now=moment + timedelta(minutes=1))

        assert (first.finished, second.finished) == (1, 0)

    async def test_ignores_session_released_before_deadline(
        self, db, printers, make_user, outbox
    ):
        user = await make_user()
        await printers_svc.occupy(db, user, printers[0].id, 60, now=NOON)
        await printers_svc.release(db, user, printers[0].id, now=NOON + timedelta(minutes=10))
        await db.commit()

        report = await reminders.reconcile(db, now=NOON + timedelta(minutes=61))

        assert report.finished == 0


class TestUnclaimed:
    async def test_pings_owner_and_queue_after_an_hour(self, db, printers, make_user, outbox):
        owner = await make_user(name="Анна")
        other = await make_user()
        waiting = await make_user()
        await printers_svc.occupy(db, owner, printers[0].id, 60, now=NOON)
        await printers_svc.occupy(db, other, printers[1].id, 600, now=NOON)
        await queue_svc.join(db, waiting.id, now=NOON)
        await reminders.reconcile(db, now=NOON + timedelta(minutes=61))
        outbox.clear()

        report = await reminders.reconcile(db, now=NOON + timedelta(minutes=125))

        assert report.unclaimed == 1
        owner_messages = [text for chat, text in outbox if chat == owner.tg_chat_id]
        queue_messages = [text for chat, text in outbox if chat == waiting.tg_chat_id]
        assert "Заберите" in owner_messages[0] or "заберите" in owner_messages[0]
        assert "не забрали" in queue_messages[0]

    async def test_pings_only_once(self, db, printers, make_user, outbox):
        await printers_svc.occupy(db, await make_user(), printers[0].id, 60, now=NOON)
        await reminders.reconcile(db, now=NOON + timedelta(minutes=61))

        first = await reminders.reconcile(db, now=NOON + timedelta(minutes=125))
        second = await reminders.reconcile(db, now=NOON + timedelta(minutes=130))

        assert (first.unclaimed, second.unclaimed) == (1, 0)

    async def test_no_ping_before_an_hour(self, db, printers, make_user, outbox):
        await printers_svc.occupy(db, await make_user(), printers[0].id, 60, now=NOON)
        await reminders.reconcile(db, now=NOON + timedelta(minutes=61))

        report = await reminders.reconcile(db, now=NOON + timedelta(minutes=100))

        assert report.unclaimed == 0


class TestOfferExpiry:
    async def test_expired_offer_passes_to_next(self, db, printers, make_user, outbox):
        owner = await make_user()
        other = await make_user()
        first = await make_user()
        second = await make_user()
        await printers_svc.occupy(db, owner, printers[0].id, 60, now=NOON)
        await printers_svc.occupy(db, other, printers[1].id, 600, now=NOON)
        await queue_svc.join(db, first.id, now=NOON)
        await queue_svc.join(db, second.id, now=NOON + timedelta(seconds=1))
        await printers_svc.release(db, owner, printers[0].id, now=NOON)
        await db.commit()
        outbox.clear()

        report = await reminders.reconcile(db, now=NOON + timedelta(minutes=31))

        assert report.expired_offers == 1
        assert [text for chat, text in outbox if chat == first.tg_chat_id][0].startswith("Время")
        assert "свободен" in [text for chat, text in outbox if chat == second.tg_chat_id][0]

    async def test_offer_is_not_expired_early(self, db, printers, make_user, outbox):
        owner = await make_user()
        other = await make_user()
        waiting = await make_user()
        await printers_svc.occupy(db, owner, printers[0].id, 60, now=NOON)
        await printers_svc.occupy(db, other, printers[1].id, 600, now=NOON)
        await queue_svc.join(db, waiting.id, now=NOON)
        await printers_svc.release(db, owner, printers[0].id, now=NOON)
        await db.commit()

        report = await reminders.reconcile(db, now=NOON + timedelta(minutes=20))

        assert report.expired_offers == 0

    async def test_night_offer_survives_until_morning(self, db, printers, make_user, outbox):
        """Правило 6 в связке с планировщиком: ночью окно не сгорает."""
        night = datetime(2026, 8, 11, 0, 0, tzinfo=UTC)  # 03:00 по Никосии
        owner = await make_user()
        other = await make_user()
        waiting = await make_user()
        await printers_svc.occupy(db, owner, printers[0].id, 60, now=night)
        await printers_svc.occupy(db, other, printers[1].id, 600, now=night)
        await queue_svc.join(db, waiting.id, now=night)
        await printers_svc.release(db, owner, printers[0].id, now=night)
        await db.commit()

        at_four = await reminders.reconcile(db, now=night + timedelta(hours=1))
        at_dawn = await reminders.reconcile(db, now=night + timedelta(hours=5, minutes=45))

        assert at_four.expired_offers == 0  # 04:00 — окно ещё не тикало
        assert at_dawn.expired_offers == 1  # 08:45 — утро наступило, время вышло


class TestIdempotence:
    async def test_quiet_run_touches_nothing(self, db, printers, outbox):
        report = await reminders.reconcile(db, now=NOON)

        assert report.touched == 0
        assert outbox == []

    async def test_catches_up_after_long_downtime(self, db, printers, make_user, outbox):
        """Приложение лежало сутки: первая же сверка доводит всё до правды."""
        printer_id = printers[0].id
        owner = await make_user()
        waiting = await make_user()
        await printers_svc.occupy(db, owner, printer_id, 60, now=NOON)
        await printers_svc.occupy(db, await make_user(), printers[1].id, 60, now=NOON)
        await queue_svc.join(db, waiting.id, now=NOON)
        await db.commit()

        report = await reminders.reconcile(db, now=NOON + timedelta(days=1))

        assert report.finished == 2
        assert report.unclaimed == 2
        db.expire_all()
        assert (await db.get(Printer, printer_id)).status == PrinterStatus.DONE_WAIT

        again = await reminders.reconcile(db, now=NOON + timedelta(days=1, minutes=1))
        assert again.touched == 0

    async def test_flags_are_recorded(self, db, printers, make_user, outbox):
        user = await make_user()
        result = await printers_svc.occupy(db, user, printers[0].id, 60, now=NOON)
        await db.commit()

        await reminders.reconcile(db, now=NOON + timedelta(minutes=50))
        await reminders.reconcile(db, now=NOON + timedelta(minutes=61))
        await reminders.reconcile(db, now=NOON + timedelta(minutes=125))

        session = await db.get(PrintSession, result.session_id)
        assert session.warned_at is not None
        assert session.finished_notified_at is not None
        assert session.unclaimed_notified_at is not None
        assert session.status == SessionStatus.DONE_WAIT


class TestSchedulerWiring:
    def test_scheduler_has_one_job(self):
        from app.scheduler import create_scheduler

        scheduler = create_scheduler()

        assert [job.id for job in scheduler.get_jobs()] == ["reconcile"]

    async def test_expired_entry_status_is_final(self, db, printers, make_user, outbox):
        owner = await make_user()
        other = await make_user()
        waiting = await make_user()
        await printers_svc.occupy(db, owner, printers[0].id, 60, now=NOON)
        await printers_svc.occupy(db, other, printers[1].id, 600, now=NOON)
        join = await queue_svc.join(db, waiting.id, now=NOON)
        await printers_svc.release(db, owner, printers[0].id, now=NOON)
        await db.commit()

        await reminders.reconcile(db, now=NOON + timedelta(minutes=31))

        entry = await db.get(QueueEntry, join.entry_id)
        assert entry.status == QueueStatus.EXPIRED
