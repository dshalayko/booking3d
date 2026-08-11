from datetime import UTC, datetime

import pytest

from app.api.kiosk import duration_options
from app.config import settings
from app.enums import PrinterStatus
from app.models import Printer
from app.services import auth
from app.services import printers as printers_svc
from app.services import queue as queue_svc


@pytest.fixture(autouse=True)
def clean_limiters():
    auth.pin_limiter._failures.clear()
    auth.pin_limiter._locked_until.clear()
    yield


async def enroll(client):
    await client.get(f"/kiosk/enroll?secret={settings.kiosk_enroll_secret}")


class TestBoard:
    async def test_board_is_open_without_login(self, client, printers):
        response = await client.get("/")

        assert response.status_code == 200
        assert "Свободен" in response.text
        assert "P2S #1" in response.text and "P2S #2" in response.text

    async def test_board_shows_who_is_printing(self, client, db, printers, make_user):
        user = await make_user(name="Иван")
        await printers_svc.occupy(db, user, printers[0].id, 120)
        await db.commit()

        response = await client.get("/")

        assert "Печатает" in response.text
        assert "Иван" in response.text

    async def test_board_shows_part_waiting_to_be_taken(self, client, db, printers, make_user):
        user = await make_user()
        await printers_svc.occupy(db, user, printers[0].id, 60)
        await printers_svc.mark_done_wait(db, printers[0].id)
        await db.commit()

        response = await client.get("/")

        assert "Заберите деталь" in response.text

    async def test_board_shows_queue_and_reservation(self, client, db, printers, make_user):
        owner = await make_user()
        other = await make_user()
        waiting = await make_user(name="Анна")
        await printers_svc.occupy(db, owner, printers[0].id, 60)
        await printers_svc.occupy(db, other, printers[1].id, 60)
        await queue_svc.join(db, waiting.id)
        await printers_svc.release(db, owner, printers[0].id)
        await db.commit()

        response = await client.get("/")

        assert "Зарезервирован" in response.text
        assert "Анна" in response.text

    async def test_partial_returns_only_the_fragment(self, client, printers):
        response = await client.get("/partials/printers")

        assert response.status_code == 200
        assert "<html" not in response.text
        assert "P2S #1" in response.text

    async def test_hero_button_offers_queue_when_all_busy(self, client, db, printers, make_user):
        for printer in printers:
            await printers_svc.occupy(db, await make_user(), printer.id, 60)
        await db.commit()

        response = await client.get("/")

        assert "Встать в очередь" in response.text
        assert "все принтеры заняты" in response.text


class TestOccupy:
    async def test_form_shows_keypad(self, client, printers):
        await enroll(client)

        response = await client.get(f"/occupy/{printers[0].id}")

        assert "PIN" in response.text
        assert "до утра" in response.text or "12 ч" in response.text

    async def test_form_refuses_busy_printer_before_pin(self, client, db, printers, make_user):
        """Иначе человек введёт PIN, выберет время и только тогда узнает об отказе."""
        printer_id = printers[0].id
        await printers_svc.occupy(db, await make_user(), printer_id, 60)
        await db.commit()
        await enroll(client)

        response = await client.get(f"/occupy/{printer_id}", headers={"accept": "text/html"})

        assert response.status_code == 409
        assert "уже занят" in response.text
        assert "keypad" not in response.text

    async def test_occupy_from_kiosk(self, client, db, printers, make_user):
        printer_id = printers[0].id
        await make_user(name="Пётр", pin="4242")
        await enroll(client)

        response = await client.post(
            f"/occupy/{printer_id}", data={"pin": "4242", "minutes": "120"}
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?flash=occupied"
        db.expire_all()
        assert (await db.get(Printer, printer_id)).status == PrinterStatus.PRINTING

    async def test_second_printer_asks_pin_again(self, client, printers, make_user):
        """Занял и отошёл — следующий у планшета вводит свой PIN, а не чужой."""
        await make_user(pin="4242")
        await enroll(client)

        await client.post(f"/occupy/{printers[0].id}", data={"pin": "4242", "minutes": "60"})
        response = await client.get(f"/occupy/{printers[1].id}")

        assert "keypad" in response.text

    async def test_occupy_needs_kiosk_device(self, client, printers, make_user):
        await make_user(pin="4242")

        response = await client.post(
            f"/occupy/{printers[0].id}", data={"pin": "4242", "minutes": "60"}
        )

        assert response.status_code == 403

    async def test_wrong_pin_shows_error_screen(self, client, printers, make_user):
        await make_user(pin="4242")
        await enroll(client)

        response = await client.post(
            f"/occupy/{printers[0].id}",
            data={"pin": "0000", "minutes": "60"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 401
        assert "Неверный PIN" in response.text

    async def test_busy_printer_shows_error_screen(self, client, db, printers, make_user):
        await printers_svc.occupy(db, await make_user(), printers[0].id, 60)
        await db.commit()
        await make_user(pin="4242")
        await enroll(client)

        response = await client.post(
            f"/occupy/{printers[0].id}",
            data={"pin": "4242", "minutes": "60"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 409
        assert "уже занят" in response.text

    async def test_queue_jumping_is_refused_on_screen(self, client, db, printers, make_user):
        """Правило 7 глазами постороннего у киоска."""
        owner = await make_user()
        other = await make_user()
        waiting = await make_user()
        await printers_svc.occupy(db, owner, printers[0].id, 60)
        await printers_svc.occupy(db, other, printers[1].id, 60)
        await queue_svc.join(db, waiting.id)
        await printers_svc.release(db, owner, printers[0].id)
        await db.commit()
        await make_user(pin="4242")
        await enroll(client)

        response = await client.post(
            f"/occupy/{printers[0].id}",
            data={"pin": "4242", "minutes": "60"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 409
        assert "зарезервирован" in response.text.lower()


class TestReleaseAndQueue:
    async def test_release_frees_printer(self, client, db, printers, make_user):
        printer_id = printers[0].id
        owner = await make_user(pin="4242")
        await printers_svc.occupy(db, owner, printer_id, 60)
        await db.commit()
        await enroll(client)

        response = await client.post(f"/release/{printer_id}", data={"pin": "4242"})

        assert response.headers["location"] == "/?flash=released"
        db.expire_all()
        assert (await db.get(Printer, printer_id)).status == PrinterStatus.FREE

    async def test_release_asks_pin_again(self, client, db, printers, make_user):
        """Освободил — принтер сразу занимает следующий, своим PIN."""
        owner = await make_user(pin="4242")
        await printers_svc.occupy(db, owner, printers[0].id, 60)
        await db.commit()
        await enroll(client)

        await client.post(f"/release/{printers[0].id}", data={"pin": "4242"})
        response = await client.get(f"/occupy/{printers[0].id}")

        assert "keypad" in response.text

    async def test_anyone_can_release(self, client, db, printers, make_user):
        """Правило 9: снял чужую деталь — освободил принтер."""
        printer_id = printers[0].id
        owner = await make_user()
        await make_user(pin="7777")
        await printers_svc.occupy(db, owner, printer_id, 60)
        await printers_svc.mark_done_wait(db, printer_id)
        await db.commit()
        await enroll(client)

        response = await client.post(f"/release/{printer_id}", data={"pin": "7777"})

        assert response.status_code == 303
        db.expire_all()
        assert (await db.get(Printer, printer_id)).status == PrinterStatus.FREE

    async def test_join_and_leave_queue(self, client, db, printers, make_user):
        user_id = (await make_user(pin="4242")).id
        for printer in printers:
            await printers_svc.occupy(db, await make_user(), printer.id, 60)
        await db.commit()
        await enroll(client)

        joined = await client.post("/queue/join", data={"pin": "4242"})
        assert joined.headers["location"] == "/?flash=queued"
        db.expire_all()
        assert await queue_svc.position_of(db, user_id) == 1

        left = await client.post("/queue/leave", data={"pin": "4242"})
        assert left.headers["location"] == "/?flash=left"
        db.expire_all()
        assert await queue_svc.position_of(db, user_id) is None

    async def test_queue_asks_pin_again(self, client, db, printers, make_user):
        """Встал в очередь — выход из неё требует PIN заново, как и всё остальное."""
        await make_user(pin="4242")
        for printer in printers:
            await printers_svc.occupy(db, await make_user(), printer.id, 60)
        await db.commit()
        await enroll(client)

        await client.post("/queue/join", data={"pin": "4242"})
        response = await client.get("/queue/leave")

        assert "keypad" in response.text

    async def test_flash_message_is_shown(self, client, printers):
        response = await client.get("/?flash=released")

        assert "Принтер освобождён" in response.text

    async def test_unknown_flash_is_ignored(self, client, printers):
        response = await client.get("/?flash=<script>")

        assert "<script>" not in response.text


class TestStaticAndOffline:
    async def test_service_worker_is_served_from_root(self, client):
        """Из /static/ он не смог бы контролировать весь сайт."""
        response = await client.get("/sw.js")

        assert response.status_code == 200
        assert "offline" in response.text

    async def test_offline_page_explains_what_to_do(self, client):
        response = await client.get("/offline")

        assert "Нет связи" in response.text
        assert "голосом" in response.text

    async def test_manifest_and_css_exist(self, client):
        assert (await client.get("/static/manifest.webmanifest")).status_code == 200
        assert (await client.get("/static/app.css")).status_code == 200
        assert (await client.get("/static/app.js")).status_code == 200


class TestDurations:
    def test_night_option_counts_until_morning(self):
        evening = datetime(2026, 8, 10, 19, 0, tzinfo=UTC)  # 22:00 в Никосии

        options = duration_options(evening)

        night = [option for option in options if option["label"] == "до утра"][0]
        assert night["minutes"] == 11 * 60  # с 22:00 до 09:00

    def test_night_option_hidden_right_before_morning(self):
        early = datetime(2026, 8, 10, 5, 55, tzinfo=UTC)  # 08:55 в Никосии

        labels = [option["label"] for option in duration_options(early)]

        assert "до утра" not in labels
