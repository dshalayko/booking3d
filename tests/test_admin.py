import pytest
from sqlalchemy import select

from app.bot import notify
from app.config import settings
from app.enums import PrinterStatus, QueueStatus, SessionStatus
from app.models import Printer, PrintSession, QueueEntry, User
from app.services import activity as activity_svc
from app.services import auth
from app.services import printers as printers_svc
from app.services import queue as queue_svc


@pytest.fixture
def outbox() -> list[tuple[int, str]]:
    sent: list[tuple[int, str]] = []

    async def sender(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    notify.set_sender(sender)
    yield sent
    notify.set_sender(None)


async def login(client) -> None:
    await client.post("/admin/login", data={"secret": settings.admin_secret})


class TestAccess:
    async def test_dashboard_is_closed_without_secret(self, client, printers):
        response = await client.get("/admin")

        assert response.status_code == 403

    async def test_login_form_is_open(self, client):
        response = await client.get("/admin/login")

        assert response.status_code == 200
        assert "ADMIN_SECRET" in response.text

    async def test_dashboard_opens_after_login(self, client, printers, make_user):
        await make_user(name="Иван", is_admin=True)
        await login(client)

        response = await client.get("/admin")

        assert response.status_code == 200
        assert "P2S #1" in response.text
        assert "Иван" in response.text

    async def test_actions_are_closed_without_secret(self, client, printers):
        response = await client.post(f"/admin/printers/{printers[0].id}/break", data={"note": "x"})

        assert response.status_code == 403


class TestPrinterActions:
    async def test_break_cancels_print_and_tells_the_owner(
        self, client, db, printers, make_user, outbox
    ):
        printer_id = printers[0].id
        await make_user(is_admin=True)
        owner = await make_user(name="Пётр")
        owner_chat = owner.tg_chat_id
        await printers_svc.occupy(db, owner, printer_id, 240)
        await db.commit()
        await login(client)
        outbox.clear()

        response = await client.post(
            f"/admin/printers/{printer_id}/break", data={"note": "полетел хотэнд"}
        )

        assert response.status_code == 303
        db.expire_all()
        printer = await db.get(Printer, printer_id)
        assert printer.status == PrinterStatus.BROKEN
        assert printer.note == "полетел хотэнд"
        assert [text for chat, text in outbox if chat == owner_chat]

    async def test_fix_returns_printer_and_offers_it_to_queue(
        self, client, db, printers, make_user, outbox
    ):
        printer_id = printers[0].id
        admin = await make_user(is_admin=True)
        waiting = await make_user()
        waiting_chat = waiting.tg_chat_id
        await printers_svc.set_broken(db, admin, printer_id)
        await printers_svc.set_broken(db, admin, printers[1].id)
        await queue_svc.join(db, waiting.id)
        await db.commit()
        await login(client)
        outbox.clear()

        await client.post(f"/admin/printers/{printer_id}/fix")

        db.expire_all()
        assert (await db.get(Printer, printer_id)).status == PrinterStatus.FREE
        assert [text for chat, text in outbox if chat == waiting_chat]

    async def test_cancel_records_reason_and_frees_printer(
        self, client, db, printers, make_user, outbox
    ):
        printer_id = printers[0].id
        await make_user(is_admin=True)
        owner = await make_user()
        owner_chat = owner.tg_chat_id
        result = await printers_svc.occupy(db, owner, printer_id, 240)
        await db.commit()
        await login(client)
        outbox.clear()

        await client.post(
            f"/admin/printers/{printer_id}/cancel", data={"reason": "печать провалилась"}
        )

        db.expire_all()
        session = await db.get(PrintSession, result.session_id)
        assert session.status == SessionStatus.CANCELLED
        assert session.cancel_reason == "печать провалилась"
        assert (await db.get(Printer, printer_id)).status == PrinterStatus.FREE
        assert "печать провалилась" in "\n".join(
            text for chat, text in outbox if chat == owner_chat
        )

    async def test_cancel_without_reason_is_refused(self, client, db, printers, make_user):
        """Человек должен понять, за что сняли его печать."""
        printer_id = printers[0].id
        await make_user(is_admin=True)
        await printers_svc.occupy(db, await make_user(), printer_id, 240)
        await db.commit()
        await login(client)

        response = await client.post(
            f"/admin/printers/{printer_id}/cancel", data={"reason": "   "}
        )

        assert response.status_code == 400
        db.expire_all()
        assert (await db.get(Printer, printer_id)).status == PrinterStatus.PRINTING

    async def test_action_without_any_admin_in_db_explains_itself(
        self, client, db, printers, make_user
    ):
        await printers_svc.occupy(db, await make_user(), printers[0].id, 60)
        await db.commit()
        await login(client)

        response = await client.post(
            f"/admin/printers/{printers[0].id}/cancel", data={"reason": "тест"}
        )

        assert response.status_code == 409
        assert "make_admin" in response.text


class TestQueueAndUsers:
    async def test_remove_from_queue(self, client, db, printers, make_user, outbox):
        await make_user(is_admin=True)
        waiting = await make_user()
        waiting_id, waiting_chat = waiting.id, waiting.tg_chat_id
        for printer in printers:
            await printers_svc.occupy(db, await make_user(), printer.id, 60)
        join = await queue_svc.join(db, waiting.id)
        await db.commit()
        await login(client)
        outbox.clear()

        await client.post(f"/admin/queue/{waiting_id}/remove")

        db.expire_all()
        assert (await db.get(QueueEntry, join.entry_id)).status == QueueStatus.LEFT
        assert [text for chat, text in outbox if chat == waiting_chat]

    async def test_rename_fixes_a_typo_in_the_login(
        self, client, db, printers, make_user, outbox
    ):
        await make_user(is_admin=True)
        person = await make_user(name="d_shalyako")
        person_id, person_chat = person.id, person.tg_chat_id
        await db.commit()
        await login(client)
        outbox.clear()

        response = await client.post(
            f"/admin/users/{person_id}/name", data={"name": " D_Shalayko "}
        )

        assert response.status_code == 303
        db.expire_all()
        assert (await db.get(User, person_id)).name == "d_shalayko"
        # Под логином человека видно на планшете, поэтому правку он получает в бот
        assert [text for chat, text in outbox if chat == person_chat]

    async def test_rename_keeps_the_pin_and_the_open_print(
        self, client, db, printers, make_user, outbox
    ):
        await make_user(is_admin=True)
        person = await make_user(name="d_shalyako", pin="4242")
        person_id = person.id
        occupied = await printers_svc.occupy(db, person, printers[0].id, 60)
        await db.commit()
        await login(client)

        await client.post(f"/admin/users/{person_id}/name", data={"name": "d_shalayko"})

        db.expire_all()
        assert (await auth.user_by_pin(db, "4242")).id == person_id
        session = await db.get(PrintSession, occupied.session_id)
        assert session.user_id == person_id
        assert session.status == SessionStatus.PRINTING

    async def test_rename_rejects_a_login_someone_else_has(
        self, client, db, printers, make_user, outbox
    ):
        await make_user(is_admin=True)
        await make_user(name="d_shalayko")
        person = await make_user(name="a_petrov")
        person_id = person.id
        await db.commit()
        await login(client)
        outbox.clear()

        response = await client.post(
            f"/admin/users/{person_id}/name", data={"name": "d_shalayko"}
        )

        assert response.status_code == 409
        db.expire_all()
        assert (await db.get(User, person_id)).name == "a_petrov"
        assert not outbox

    async def test_rename_rejects_what_is_not_a_login(
        self, client, db, printers, make_user, outbox
    ):
        await make_user(is_admin=True)
        person = await make_user(name="a_petrov")
        person_id = person.id
        await db.commit()
        await login(client)
        outbox.clear()

        response = await client.post(f"/admin/users/{person_id}/name", data={"name": "Анна"})

        assert response.status_code == 400
        db.expire_all()
        assert (await db.get(User, person_id)).name == "a_petrov"
        assert not outbox

    async def test_rename_is_closed_without_login(self, client, db, make_user):
        person = await make_user(name="a_petrov")
        person_id = person.id
        await db.commit()

        response = await client.post(f"/admin/users/{person_id}/name", data={"name": "b_ivanov"})

        assert response.status_code == 403
        db.expire_all()
        assert (await db.get(User, person_id)).name == "a_petrov"

    async def test_reset_pin_sends_it_only_to_telegram(
        self, client, db, printers, make_user, outbox
    ):
        await make_user(is_admin=True)
        person = await make_user(pin="4242")
        person_id, person_chat = person.id, person.tg_chat_id
        await db.commit()
        await login(client)
        outbox.clear()

        response = await client.post(f"/admin/users/{person_id}/pin")

        assert response.status_code == 303
        # PIN не должен попасть в URL редиректа — он оседает в логах и истории
        assert "4242" not in response.headers["location"]
        message = [text for chat, text in outbox if chat == person_chat][0]
        new_pin = message.split("<b>")[1].split("</b>")[0]
        db.expire_all()
        assert (await auth.user_by_pin(db, new_pin)).id == person_id


class TestActivityLog:
    async def test_log_covers_the_whole_cycle(self, db, printers, make_user):
        owner = await make_user(name="Иван")
        stranger = await make_user(name="Анна")
        await printers_svc.occupy(db, owner, printers[0].id, 60)
        await printers_svc.mark_done_wait(db, printers[0].id)
        await printers_svc.release(db, stranger, printers[0].id)
        await db.commit()

        events = await activity_svc.recent(db)
        text = "\n".join(event.text for event in events)

        assert "P2S #1 — занят: Иван" in text
        assert "деталь забрали (Анна)" in text

    async def test_log_shows_queue_life(self, db, printers, make_user):
        owner = await make_user()
        other = await make_user()
        waiting = await make_user(name="Пётр")
        await printers_svc.occupy(db, owner, printers[0].id, 60)
        await printers_svc.occupy(db, other, printers[1].id, 60)
        await queue_svc.join(db, waiting.id)
        await printers_svc.release(db, owner, printers[0].id)
        await queue_svc.leave(db, waiting.id)
        await db.commit()

        text = "\n".join(event.text for event in await activity_svc.recent(db))

        assert "В очередь: Пётр" in text
        assert "Приглашение на P2S #1: Пётр" in text
        assert "Выход из очереди: Пётр" in text

    async def test_log_shows_cancellation_reason(self, db, printers, make_user):
        admin = await make_user(name="Админ", is_admin=True)
        owner = await make_user(name="Иван")
        await printers_svc.occupy(db, owner, printers[0].id, 60)
        await printers_svc.release(db, admin, printers[0].id, reason="печать провалилась")
        await db.commit()

        text = "\n".join(event.text for event in await activity_svc.recent(db))

        assert "печать снята, Админ — печать провалилась" in text

    async def test_log_is_newest_first_and_bounded(self, db, printers, make_user):
        for _ in range(3):
            user = await make_user()
            await printers_svc.occupy(db, user, printers[0].id, 60)
            await printers_svc.release(db, user, printers[0].id)
        await db.commit()

        events = await activity_svc.recent(db, limit=4)

        assert len(events) == 4
        assert events == sorted(events, key=lambda event: event.at, reverse=True)

    async def test_empty_log_is_fine(self, db, printers):
        assert await activity_svc.recent(db) == []

    async def test_log_is_visible_in_dashboard(self, client, db, printers, make_user):
        await make_user(is_admin=True)
        owner = await make_user(name="Иван")
        await printers_svc.occupy(db, owner, printers[0].id, 60)
        await db.commit()
        await login(client)

        response = await client.get("/admin")

        assert "P2S #1 — занят: Иван" in response.text

    async def test_sessions_keep_history_after_release(self, db, printers, make_user):
        """Журнал строится из таблиц, поэтому закрытые сессии не удаляются."""
        user = await make_user()
        await printers_svc.occupy(db, user, printers[0].id, 60)
        await printers_svc.release(db, user, printers[0].id)
        await db.commit()

        rows = (await db.scalars(select(PrintSession))).all()

        assert len(rows) == 1
        assert rows[0].ended_at is not None
