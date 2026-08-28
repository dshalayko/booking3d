
import pytest
from sqlalchemy import select

from app.admin import SECTIONS
from app.bot import notify
from app.config import settings
from app.enums import (
    MachineKind,
    MachineStatus,
    ReservationStatus,
    SessionStatus,
)
from app.models import (
    FeedbackRequest,
    Machine,
    MachineSession,
    Reservation,
    Room,
    TextOverride,
    User,
)
from app.services import activity as activity_svc
from app.services import auth, booking_policy
from app.services import machines as machines_svc
from app.services import queue as queue_svc
from app.services import reservations as reservations_svc


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
        assert "/static/app.js" not in response.text
        assert "<script" not in response.text

    async def test_dashboard_opens_after_login(self, client, printers, make_user):
        await make_user(name="Иван", is_admin=True)
        await login(client)

        response = await client.get("/admin")

        assert response.status_code == 200
        assert "P2S #1" in response.text
        assert "/static/app.js" not in response.text
        assert "<script" not in response.text

    async def test_actions_are_closed_without_secret(self, client, printers):
        response = await client.post(f"/admin/machines/{printers[0].id}/break", data={"note": "x"})

        assert response.status_code == 403


class TestPanel:
    """Ядро панели: разделы объявляются списком, а не разбросаны по коду.

    Проверки идут по реестру, а не по написанному руками списку адресов: новый
    раздел проверяется ими сам, и забыть его здесь нельзя.
    """

    async def test_every_section_is_closed_without_secret(self, client, printers):
        """Доступ висит на общем роутере — раздел не может открыться наружу
        потому, что в нём забыли проверку."""
        for section in SECTIONS:
            assert (await client.get(section.path)).status_code == 403, section.slug

    async def test_every_section_opens_and_shows_the_whole_menu(
        self, client, printers, make_user
    ):
        """Меню рисуется из реестра, поэтому с любой страницы видны все
        разделы — раздел без пункта в меню невозможен."""
        await make_user(is_admin=True)
        await login(client)

        for section in SECTIONS:
            response = await client.get(section.path)

            assert response.status_code == 200, section.slug
            for item in SECTIONS:
                assert f'href="{item.path}"' in response.text, (section.slug, item.slug)

    async def test_people_section_lists_everyone(self, client, printers, make_user):
        await make_user(name="Иван", is_admin=True)
        await login(client)

        response = await client.get("/admin/people")

        assert "Иван" in response.text

    async def test_feedback_section_lists_messages_newest_first(
        self, client, db, printers, make_user
    ):
        person = await make_user(name="d_shalayko", is_admin=True)
        db.add_all(
            [
                FeedbackRequest(
                    user_id=person.id, username="First", message="Old request"
                ),
                FeedbackRequest(
                    user_id=person.id, username="Second", message="New request"
                ),
            ]
        )
        await db.commit()
        await login(client)

        response = await client.get("/admin/feedback")

        assert response.status_code == 200
        assert response.text.index("New request") < response.text.index("Old request")
        assert "First" in response.text and "Second" in response.text


class TestBookingRules:
    async def test_admin_can_enable_and_disable_extended_limit(
        self, client, db, printers, make_user
    ):
        await make_user(is_admin=True)
        await login(client)

        page = await client.get("/admin/rules")
        assert page.status_code == 200
        assert "Несколько машин на человека" in page.text
        assert "checked" not in page.text

        enabled = await client.post(
            "/admin/rules", data={"multi_machine_enabled": "on"}
        )
        assert enabled.status_code == 303
        assert enabled.headers["location"] == "/admin/rules?flash=rules_saved"
        db.expire_all()
        assert await booking_policy.enabled(db) is True

        await client.post("/admin/rules", data={})
        db.expire_all()
        assert await booking_policy.enabled(db) is False


class TestEditableTexts:
    async def test_admin_can_edit_export_and_reset_english_text(
        self, client, db, printers, make_user
    ):
        await make_user(is_admin=True)
        await login(client)

        page = await client.get("/admin/texts?q=UI.app_close")
        assert page.status_code == 200
        assert "UI.app_close" in page.text
        assert "Close app" in page.text

        saved = await client.post(
            "/admin/texts",
            data={"key": "UI.app_close", "value": "Exit mini app", "q": "", "page": 1},
        )
        assert saved.status_code == 303
        assert (await db.get(TextOverride, "UI.app_close")).value == "Exit mini app"

        exported = await client.get("/admin/texts/export")
        assert exported.status_code == 200
        assert exported.headers["content-disposition"] == 'attachment; filename="booking-en.json"'
        assert exported.json()["UI.app_close"] == "Exit mini app"

        reset = await client.post(
            "/admin/texts/reset",
            data={"key": "UI.app_close", "q": "", "page": 1},
        )
        assert reset.status_code == 303
        db.expire_all()
        assert await db.get(TextOverride, "UI.app_close") is None

    async def test_placeholders_cannot_be_lost(self, client, printers, make_user):
        await make_user(is_admin=True)
        await login(client)

        page = await client.get("/admin/texts?q=UNIT_HOURS")
        assert page.status_code == 200
        assert "{hours}" in page.text

        response = await client.post(
            "/admin/texts",
            data={"key": "UNIT_HOURS", "value": "hours", "q": "", "page": 1},
        )

        assert response.status_code == 400
        assert "hours" in response.text

class TestMachineActions:
    async def test_break_cancels_print_and_tells_the_owner(
        self, client, db, printers, make_user, outbox
    ):
        machine_id = printers[0].id
        await make_user(is_admin=True)
        owner = await make_user(name="Пётр")
        owner_chat = owner.tg_chat_id
        await machines_svc.occupy(db, owner, machine_id, 240)
        await db.commit()
        await login(client)
        outbox.clear()

        response = await client.post(
            f"/admin/machines/{machine_id}/break", data={"note": "полетел хотэнд"}
        )

        assert response.status_code == 303
        db.expire_all()
        printer = await db.get(Machine, machine_id)
        assert printer.status == MachineStatus.BROKEN
        assert printer.note == "полетел хотэнд"
        assert [text for chat, text in outbox if chat == owner_chat]

    async def test_fix_returns_printer_without_legacy_queue_notifications(
        self, client, db, printers, make_user, outbox
    ):
        machine_id = printers[0].id
        admin = await make_user(is_admin=True)
        await machines_svc.set_broken(db, admin, machine_id)
        await db.commit()
        await login(client)
        outbox.clear()

        await client.post(f"/admin/machines/{machine_id}/fix")

        db.expire_all()
        assert (await db.get(Machine, machine_id)).status == MachineStatus.FREE
        assert outbox == []

    async def test_cancel_records_reason_and_frees_printer(
        self, client, db, printers, make_user, outbox
    ):
        machine_id = printers[0].id
        await make_user(is_admin=True)
        owner = await make_user()
        owner_chat = owner.tg_chat_id
        result = await machines_svc.occupy(db, owner, machine_id, 240)
        await db.commit()
        await login(client)
        outbox.clear()

        await client.post(
            f"/admin/machines/{machine_id}/cancel", data={"reason": "печать провалилась"}
        )

        db.expire_all()
        session = await db.get(MachineSession, result.session_id)
        assert session.status == SessionStatus.CANCELLED
        assert session.cancel_reason == "печать провалилась"
        assert (await db.get(Machine, machine_id)).status == MachineStatus.FREE
        assert "печать провалилась" in "\n".join(
            text for chat, text in outbox if chat == owner_chat
        )

    async def test_cancel_without_reason_is_refused(self, client, db, printers, make_user):
        """Человек должен понять, за что сняли его печать."""
        machine_id = printers[0].id
        await make_user(is_admin=True)
        await machines_svc.occupy(db, await make_user(), machine_id, 240)
        await db.commit()
        await login(client)

        response = await client.post(
            f"/admin/machines/{machine_id}/cancel", data={"reason": "   "}
        )

        assert response.status_code == 400
        db.expire_all()
        assert (await db.get(Machine, machine_id)).status == MachineStatus.PRINTING

    async def test_action_without_any_admin_in_db_explains_itself(
        self, client, db, printers, make_user
    ):
        await machines_svc.occupy(db, await make_user(), printers[0].id, 60)
        await db.commit()
        await login(client)

        response = await client.post(
            f"/admin/machines/{printers[0].id}/cancel", data={"reason": "тест"}
        )

        assert response.status_code == 409
        assert "make_admin" in response.text


class TestUsers:
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
        occupied = await machines_svc.occupy(db, person, printers[0].id, 60)
        await db.commit()
        await login(client)

        await client.post(f"/admin/users/{person_id}/name", data={"name": "d_shalayko"})

        db.expire_all()
        assert (await auth.user_by_pin(db, "4242")).id == person_id
        session = await db.get(MachineSession, occupied.session_id)
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
        await machines_svc.occupy(db, owner, printers[0].id, 60)
        await machines_svc.mark_done_wait(db, printers[0].id)
        await machines_svc.release(db, stranger, printers[0].id)
        await db.commit()

        events = await activity_svc.recent(db)
        text = "\n".join(event.text for event in events)

        assert "P2S #1 — занят: Иван" in text
        assert "деталь забрали (Анна)" in text

    async def test_log_shows_queue_life(self, db, room, printers, make_user):
        owner = await make_user()
        other = await make_user()
        waiting = await make_user(name="Пётр")
        await machines_svc.occupy(db, owner, printers[0].id, 60)
        await machines_svc.occupy(db, other, printers[1].id, 60)
        await queue_svc.join(db, waiting.id, room.id, MachineKind.PRINTER)
        await machines_svc.release(db, owner, printers[0].id)
        await queue_svc.leave(db, waiting.id, room.id)
        await db.commit()

        text = "\n".join(event.text for event in await activity_svc.recent(db))

        assert "В очередь на принтер (Мастерская): Пётр" in text
        assert "Приглашение на P2S #1: Пётр" not in text
        assert "Выход из очереди: Пётр" in text

    async def test_log_shows_cancellation_reason(self, db, printers, make_user):
        admin = await make_user(name="Админ", is_admin=True)
        owner = await make_user(name="Иван")
        await machines_svc.occupy(db, owner, printers[0].id, 60)
        await machines_svc.release(db, admin, printers[0].id, reason="печать провалилась")
        await db.commit()

        text = "\n".join(event.text for event in await activity_svc.recent(db))

        assert "работа снята, Админ — печать провалилась" in text

    async def test_log_is_newest_first_and_bounded(self, db, printers, make_user):
        for _ in range(3):
            user = await make_user()
            await machines_svc.occupy(db, user, printers[0].id, 60)
            await machines_svc.release(db, user, printers[0].id)
        await db.commit()

        events = await activity_svc.recent(db, limit=4)

        assert len(events) == 4
        assert events == sorted(events, key=lambda event: event.at, reverse=True)

    async def test_empty_log_is_fine(self, db, printers):
        assert await activity_svc.recent(db) == []

    async def test_log_is_visible_in_its_section(self, client, db, printers, make_user):
        await make_user(is_admin=True)
        owner = await make_user(name="Иван")
        await machines_svc.occupy(db, owner, printers[0].id, 60)
        await db.commit()
        await login(client)

        response = await client.get("/admin/log")

        assert "P2S #1 — занят: Иван" in response.text

    async def test_sessions_keep_history_after_release(self, db, printers, make_user):
        """Журнал строится из таблиц, поэтому закрытые сессии не удаляются."""
        user = await make_user()
        await machines_svc.occupy(db, user, printers[0].id, 60)
        await machines_svc.release(db, user, printers[0].id)
        await db.commit()

        rows = (await db.scalars(select(MachineSession))).all()

        assert len(rows) == 1
        assert rows[0].ended_at is not None


class TestMachinesTab:
    """Состав парка правится отсюда, а не правкой .env и перезапуском."""

    async def test_tab_lists_the_park_and_links_from_the_summary(
        self, client, db, printers, engravers, make_user
    ):
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        summary = await client.get("/admin")
        response = await client.get("/admin/machines")

        assert '/admin/machines"' in summary.text, "со сводки должна быть ссылка на раздел"
        assert response.status_code == 200
        assert "P2S #1" in response.text and "Гравёр #1" in response.text

    async def test_tab_is_closed_without_secret(self, client, printers):
        assert (await client.get("/admin/machines")).status_code == 403
        assert (await client.post("/admin/machines", data={
            "name": "Гравёр #9", "kind": "engraver"
        })).status_code == 403

    async def test_add_engraver(self, client, db, room, printers, make_user):
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        response = await client.post(
            "/admin/machines",
            data={"name": "Гравёр #1", "kind": "engraver", "room_id": room.id},
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/admin/machines?flash=machine_added"
        machine = await db.scalar(select(Machine).where(Machine.name == "Гравёр #1"))
        assert machine.kind == MachineKind.ENGRAVER
        assert machine.status == MachineStatus.FREE

    async def test_added_machine_shows_up_on_the_wall(self, client, db, room, printers, make_user):
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        await client.post(
            "/admin/machines",
            data={"name": "Гравёр #1", "kind": "engraver", "room_id": room.id},
        )

        # Планшет не привязан к помещению, поэтому доску открываем по адресу
        # комнаты — так же, как это делает планшет со своей меткой.
        board = await client.get(f"/room/{room.id}")
        assert "Гравёр #1" in board.text
        assert "Гравировщики" in board.text

    async def test_add_refuses_a_taken_name(self, client, db, room, printers, make_user):
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        response = await client.post(
            "/admin/machines",
            data={"name": "P2S #1", "kind": "engraver", "room_id": room.id},
        )

        assert response.status_code == 409

    async def test_rename_machine(self, client, db, printers, make_user):
        machine_id = printers[0].id
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        response = await client.post(
            f"/admin/machines/{machine_id}/name", data={"name": "Bambu X1"}
        )

        assert response.status_code == 303
        db.expire_all()
        assert (await db.get(Machine, machine_id)).name == "Bambu X1"

    async def test_delete_machine_without_history(self, client, db, printers, make_user):
        machine_id = printers[1].id
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        response = await client.post(f"/admin/machines/{machine_id}/delete")

        assert response.status_code == 303
        db.expire_all()
        assert await db.get(Machine, machine_id) is None

    async def test_delete_is_refused_for_a_machine_with_history(
        self, client, db, printers, make_user
    ):
        machine_id = printers[0].id
        await make_user(is_admin=True)
        owner = await make_user()
        await machines_svc.occupy(db, owner, machine_id, 60)
        await machines_svc.release(db, owner, machine_id)
        await db.commit()
        await login(client)

        response = await client.post(f"/admin/machines/{machine_id}/delete")

        assert response.status_code == 409
        db.expire_all()
        assert await db.get(Machine, machine_id) is not None

    async def test_delete_leads_to_confirmation_for_every_machine(
        self, client, db, printers, make_user
    ):
        """Кнопка есть и у машины с историей: она ведёт на экран подтверждения.

        Раньше её прятали — и машину с историей нельзя было убрать ничем, кроме
        psql. Теперь прячется не кнопка, а мгновенное удаление.
        """
        await make_user(is_admin=True)
        owner = await make_user()
        await machines_svc.occupy(db, owner, printers[0].id, 60)
        await db.commit()
        await login(client)

        listing = await client.get("/admin/machines")
        confirm = await client.get(f"/admin/machines/{printers[0].id}/delete")

        assert f"/admin/machines/{printers[0].id}/delete" in listing.text
        assert f"/admin/machines/{printers[1].id}/delete" in listing.text
        assert confirm.status_code == 200
        assert "работ в журнале: 1" in confirm.text

    async def test_confirmed_delete_takes_the_machine_with_its_history(
        self, client, db, printers, make_user
    ):
        machine_id = printers[0].id
        await make_user(is_admin=True)
        owner = await make_user()
        await machines_svc.occupy(db, owner, machine_id, 60)
        await machines_svc.release(db, owner, machine_id)
        await db.commit()
        await login(client)

        response = await client.post(
            f"/admin/machines/{machine_id}/delete", data={"confirm": "1"}
        )

        assert response.status_code == 303
        db.expire_all()
        assert await db.get(Machine, machine_id) is None
        assert (
            await db.scalars(
                select(MachineSession).where(MachineSession.machine_id == machine_id)
            )
        ).all() == []


class TestPeople:
    """Заводить и удалять людей: тестовые учётки и тот, кто до бота не дошёл."""

    async def test_admin_adds_a_person_with_a_pin(self, client, db, printers, make_user):
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        response = await client.post(
            "/admin/users", data={"login": "n_novikov", "tg_chat_id": 900500, "pin": "8391"}
        )

        assert response.status_code == 303
        person = await db.scalar(select(User).where(User.name == "n_novikov"))
        assert person is not None
        # PIN — рабочий: заведённый из админки человек сразу может им пользоваться.
        assert (await auth.user_by_pin(db, "8391")).id == person.id

    async def test_added_person_cannot_take_a_busy_login(self, client, db, printers, make_user):
        await make_user(is_admin=True)
        await make_user(name="p_petrov")
        await db.commit()
        await login(client)

        response = await client.post(
            "/admin/users", data={"login": "p_petrov", "tg_chat_id": 900501, "pin": "8392"}
        )

        assert response.status_code == 409

    async def test_delete_takes_the_person_with_their_work_and_frees_the_machine(
        self, client, db, printers, make_user
    ):
        """Работа исчезает вместе с человеком, а машина возвращается в строй.

        Статус машины живёт в своей колонке: без этого шага на доске осталась бы
        «занятая» машина, за которой никого нет, и занять её было бы нельзя.
        """
        machine_id = printers[0].id
        await make_user(is_admin=True)
        owner = await make_user()
        owner_id = owner.id
        await machines_svc.occupy(db, owner, machine_id, 60)
        await db.commit()
        await login(client)

        response = await client.post(f"/admin/users/{owner_id}/delete")

        assert response.status_code == 303
        db.expire_all()
        assert await db.get(User, owner_id) is None
        assert (await db.get(Machine, machine_id)).status == MachineStatus.FREE
        assert (
            await db.scalars(select(MachineSession).where(MachineSession.user_id == owner_id))
        ).all() == []

    async def test_the_last_admin_stays(self, client, db, printers, make_user):
        """От имени админа из базы панель пишет каждое действие в журнал:
        удалив последнего, оператор закрыл бы админку сам на себя."""
        admin = await make_user(is_admin=True)
        admin_id = admin.id
        await db.commit()
        await login(client)

        response = await client.post(f"/admin/users/{admin_id}/delete")

        assert response.status_code == 409
        db.expire_all()
        assert await db.get(User, admin_id) is not None


class TestPurge:
    """Удаление вместе с историей — второй ответ на «удалить», см. purge.py."""

    async def test_room_goes_with_its_machines_and_their_history(
        self, client, db, room, printers, make_user
    ):
        room_id = room.id
        machine_id = printers[0].id
        await make_user(is_admin=True)
        owner = await make_user()
        await machines_svc.occupy(db, owner, machine_id, 60)
        await db.commit()
        await login(client)

        confirm = await client.get(f"/admin/rooms/{room_id}/delete")
        response = await client.post(f"/admin/rooms/{room_id}/delete", data={"confirm": "1"})

        assert "оборудование: 2" in confirm.text
        assert response.status_code == 303
        db.expire_all()
        assert await db.get(Room, room_id) is None
        assert await db.get(Machine, machine_id) is None
        assert (await db.scalars(select(MachineSession))).all() == []

    async def test_unconfirmed_post_still_refuses_a_room_with_history(
        self, client, db, room, printers, make_user
    ):
        """Подтверждение — поле формы: случайный POST мимо экрана подтверждения
        удаляет только то, за чем ничего не записано."""
        room_id = room.id
        await make_user(is_admin=True)
        await db.commit()
        await login(client)

        response = await client.post(f"/admin/rooms/{room_id}/delete")

        assert response.status_code == 409
        db.expire_all()
        assert await db.get(Room, room_id) is not None

class TestBookings:
    """Брони на будущее: их не видно ни на плитках, ни в очереди."""

    async def test_section_lists_bookings(self, client, db, printers, make_user, work_slot):
        await make_user(is_admin=True)
        person = await make_user(name="Анна")
        await reservations_svc.book(db, person, printers[0].id, work_slot(), 120)
        await db.commit()
        await login(client)

        response = await client.get("/admin/bookings")

        assert "Анна" in response.text
        assert "P2S #1" in response.text

    async def test_admin_cancels_a_booking(
        self, client, db, printers, make_user, outbox, work_slot
    ):
        await make_user(is_admin=True)
        person = await make_user(name="Анна")
        chat_id = person.tg_chat_id
        booking = await reservations_svc.book(db, person, printers[0].id, work_slot(), 60)
        await db.commit()
        await login(client)

        response = await client.post(
            f"/admin/bookings/{booking.reservation_id}/cancel",
            data={"reason": "машина уехала"},
        )

        assert response.status_code == 303
        db.expire_all()
        stored = await db.get(Reservation, booking.reservation_id)
        assert stored.status == ReservationStatus.CANCELLED
        assert stored.cancel_reason == "машина уехала"
        # Человек считал своё окно занятым и планировал вокруг него.
        assert any(sent_to == chat_id for sent_to, _ in outbox)

    async def test_cancelling_requires_login(self, client, db, printers, make_user, work_slot):
        await make_user(is_admin=True)
        person = await make_user()
        booking = await reservations_svc.book(db, person, printers[0].id, work_slot(), 60)
        await db.commit()

        response = await client.post(f"/admin/bookings/{booking.reservation_id}/cancel")

        assert response.status_code == 403
        db.expire_all()
        stored = await db.get(Reservation, booking.reservation_id)
        assert stored.status == ReservationStatus.BOOKED

    async def test_log_shows_booking_life(self, db, printers, make_user, work_slot):
        person = await make_user(name="Пётр")
        booking = await reservations_svc.book(db, person, printers[0].id, work_slot(), 60)
        await reservations_svc.cancel(db, person, booking.reservation_id)
        await db.commit()

        text = "\n".join(event.text for event in await activity_svc.recent(db))

        assert "Бронь P2S #1" in text
        assert "бронь на" in text and "отменена" in text
