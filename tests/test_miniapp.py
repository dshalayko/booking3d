"""Telegram Mini App.

Главное здесь — подпись `initData`: она заменила собой правило «только с планшета
по PIN» для телефонов, и если её проверка окажется дырявой, занимать машины и
отменять чужие брони сможет кто угодно из интернета. Поэтому проверок на подпись
больше, чем на экраны.

Подпись в тестах считается тем же алгоритмом, что и в проверке, но независимо от
неё: `_sign` ниже написан по описанию Telegram, а не вызовом нашей функции —
иначе тест подтверждал бы сам себя.
"""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from app.config import settings
from app.enums import MachineKind, MachineStatus, ReservationStatus
from app.models import Machine, Reservation
from app.services import machines as machines_svc
from app.services import queue as queue_svc
from app.services import reservations as reservations_svc
from app.services import schedule as schedule_svc
from app.services import telegram
from app.services.errors import BadInitData

BOT_TOKEN = "123456:test-token"


@pytest.fixture(autouse=True)
def bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подпись считается на токене бота: без него проверять нечего."""
    monkeypatch.setattr(settings, "tg_bot_token", BOT_TOKEN)


def _sign(chat_id: int, auth_date: int | None = None, token: str = BOT_TOKEN) -> str:
    """Собрать initData так, как это делает Telegram."""
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAF",
        "user": json.dumps({"id": chat_id, "first_name": "Test"}, separators=(",", ":")),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


async def open_app(client, user) -> None:
    """Пройти бутстрап: подпись Telegram в обмен на cookie сессии."""
    response = await client.post(
        "/app/session", data={"init_data": _sign(user.tg_chat_id), "next": "/app/"}
    )
    assert response.status_code == 303


def tomorrow_at(hour: int = 10) -> datetime:
    """Завтрашний рабочий час в UTC — то же, что фикстура `work_slot`.

    Не `align(now) + сутки`: бронировать можно только рабочие часы, и ночной
    прогон набора получал бы отказ вместо расписания. Своя копия, а не фикстура,
    потому что зовётся и из параметров теста, где фикстуры ещё нет.
    """
    moment = datetime.now(settings.zone) + timedelta(days=1)
    return moment.replace(hour=hour, minute=0, second=0, microsecond=0).astimezone(UTC)


class TestInitData:
    def test_valid_signature_gives_chat_id(self):
        assert telegram.check_init_data(_sign(4242)) == 4242

    def test_tampered_payload_is_refused(self):
        signed = _sign(4242)
        # Подменяем id на чужой, оставив подпись прежней — так выглядела бы
        # попытка действовать от чужого имени.
        forged = signed.replace("%22id%22%3A4242", "%22id%22%3A1")

        with pytest.raises(BadInitData):
            telegram.check_init_data(forged)

    def test_signature_of_another_bot_is_refused(self):
        with pytest.raises(BadInitData):
            telegram.check_init_data(_sign(4242, token="999:someone-elses-bot"))

    def test_stale_open_is_refused(self):
        old = int(time.time()) - telegram.INIT_DATA_MAX_AGE - 60

        with pytest.raises(BadInitData):
            telegram.check_init_data(_sign(4242, auth_date=old))

    def test_missing_hash_is_refused(self):
        with pytest.raises(BadInitData):
            telegram.check_init_data("user=%7B%22id%22%3A1%7D&auth_date=1")

    def test_empty_init_data_is_refused(self):
        with pytest.raises(BadInitData):
            telegram.check_init_data("")

    def test_without_bot_token_nothing_is_trusted(self, monkeypatch):
        """Пустой TG_BOT_TOKEN не должен превращаться в «подпись сходится»."""
        signed = _sign(4242)
        monkeypatch.setattr(settings, "tg_bot_token", "")

        with pytest.raises(BadInitData):
            telegram.check_init_data(signed)


class TestSession:
    async def test_bootstrap_page_is_shown_without_session(self, client):
        response = await client.get("/app/")

        assert response.status_code == 200
        assert "data-tg-bootstrap" in response.text
        assert "telegram-web-app.js" in response.text

    async def test_valid_open_sets_session_and_shows_board(self, client, printers, make_user):
        user = await make_user()

        response = await client.post(
            "/app/session", data={"init_data": _sign(user.tg_chat_id), "next": "/app/"}
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/app/"
        board = await client.get("/app/")
        assert "P2S #1" in board.text

    async def test_forged_open_is_refused(self, client, make_user):
        await make_user()

        response = await client.post(
            "/app/session",
            data={"init_data": "user=%7B%22id%22%3A1%7D&hash=deadbeef"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 403

    async def test_unknown_person_is_sent_to_the_bot(self, client, printers):
        """Логин спрашивает бот: второго пути регистрации быть не должно."""
        response = await client.post(
            "/app/session",
            data={"init_data": _sign(999999)},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 403
        assert "/start" in response.text

    async def test_next_cannot_leave_the_app(self, client, make_user):
        """Иначе `next` — это открытый редирект на чужой сайт."""
        user = await make_user()

        response = await client.post(
            "/app/session",
            data={"init_data": _sign(user.tg_chat_id), "next": "https://evil.example/"},
        )

        assert response.headers["location"] == "/app/"

    async def test_actions_without_session_are_refused(self, client, printers):
        response = await client.post(
            f"/app/occupy/{printers[0].id}",
            data={"minutes": "60"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 401

    async def test_my_without_session_opens_bootstrap(self, client):
        response = await client.get("/app/my")

        assert response.status_code == 200
        assert 'value="/app/my"' in response.text


class TestOpenAccess:
    """`MINIAPP_OPEN_ACCESS` — режим для проверки брон без бота и сертификата."""

    @pytest.fixture
    def open_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "miniapp_open_access", True)

    async def test_entry_offers_people_instead_of_telegram(
        self, client, open_access, make_user
    ):
        await make_user(name="a_ivanov")

        response = await client.get("/app/")

        assert "a_ivanov" in response.text
        assert 'name="as_user_id"' in response.text

    async def test_sign_in_without_signature(self, client, db, printers, open_access, make_user):
        person = await make_user()

        response = await client.post("/app/session", data={"as_user_id": person.id})

        assert response.status_code == 303
        board = await client.get("/app/")
        assert "P2S #1" in board.text

    async def test_signed_in_person_can_act(self, client, db, printers, open_access, make_user):
        person = await make_user()
        machine_id = printers[0].id
        await client.post("/app/session", data={"as_user_id": person.id})

        response = await client.post(f"/app/occupy/{machine_id}", data={"minutes": "60"})

        assert response.status_code == 303
        db.expire_all()
        assert (await db.get(Machine, machine_id)).status == MachineStatus.PRINTING

    async def test_forged_signature_is_still_refused(self, client, open_access, make_user):
        """Флаг разрешает вход без подписи, а не подделку подписи."""
        await make_user()

        response = await client.post(
            "/app/session",
            data={"init_data": "user=%7B%22id%22%3A1%7D&hash=deadbeef"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 403

    async def test_flag_off_means_no_test_entry(self, client, make_user):
        person = await make_user()

        response = await client.post(
            "/app/session",
            data={"as_user_id": person.id},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 403


class TestScreens:
    async def test_board_has_no_pin_keypad(self, client, printers, make_user):
        user = await make_user()
        await open_app(client, user)

        response = await client.get(f"/app/occupy/{printers[0].id}")

        assert response.status_code == 200
        assert "keypad" not in response.text
        assert 'action="/app/occupy/' in response.text

    async def test_occupy_from_the_phone(self, client, db, printers, make_user):
        user = await make_user()
        machine_id = printers[0].id
        await open_app(client, user)

        response = await client.post(f"/app/occupy/{machine_id}", data={"minutes": "60"})

        assert response.status_code == 303
        assert response.headers["location"] == "/app/?flash=occupied"
        db.expire_all()
        assert (await db.get(Machine, machine_id)).status == MachineStatus.PRINTING

    async def test_booking_from_the_phone(self, client, db, printers, make_user):
        user = await make_user()
        await open_app(client, user)
        start = tomorrow_at()

        response = await client.post(
            f"/app/book/{printers[0].id}",
            data={"start": start.isoformat(), "minutes": "120"},
        )

        assert response.status_code == 303
        booking = await reservations_svc.active_of_user(db, user.id)
        assert booking is not None
        assert booking.starts_at == start

    async def test_schedule_highlights_my_booking(self, client, db, printers, make_user):
        user = await make_user(name="Аня")
        await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()
        await open_app(client, user)

        response = await client.get(
            f"/app/schedule/{MachineKind.PRINTER}",
            params={"date": schedule_svc.day_of(tomorrow_at()).isoformat()},
        )

        assert "cell-mine" in response.text

    async def test_kiosk_schedule_highlights_nothing(self, client, db, printers, make_user):
        """На стене неизвестно, кто смотрит, — своих брон там нет."""
        user = await make_user(name="Аня")
        await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()

        response = await client.get(
            f"/schedule/{MachineKind.PRINTER}",
            params={"date": schedule_svc.day_of(tomorrow_at()).isoformat()},
        )

        assert "cell-mine" not in response.text
        assert "cell-booked" in response.text

    async def test_my_lists_bookings_and_cancels_them(self, client, db, printers, make_user):
        user = await make_user()
        booking = await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()
        await open_app(client, user)

        listing = await client.get("/app/my")
        assert "P2S #1" in listing.text

        response = await client.post(f"/app/booking/{booking.reservation_id}/cancel")

        assert response.status_code == 303
        db.expire_all()
        stored = await db.get(Reservation, booking.reservation_id)
        assert stored.status == ReservationStatus.CANCELLED

    async def test_cannot_cancel_someone_elses_booking(self, client, db, printers, make_user):
        owner = await make_user()
        stranger = await make_user()
        booking = await reservations_svc.book(db, owner, printers[0].id, tomorrow_at(), 60)
        await db.commit()
        await open_app(client, stranger)

        response = await client.post(
            f"/app/booking/{booking.reservation_id}/cancel",
            headers={"accept": "text/html"},
        )

        assert response.status_code == 403
        db.expire_all()
        stored = await db.get(Reservation, booking.reservation_id)
        assert stored.status == ReservationStatus.BOOKED

    async def test_queue_from_the_phone(self, client, db, printers, make_user):
        busy = await make_user()
        waiting = await make_user()
        await machines_svc.occupy(db, busy, printers[0].id, 60)
        await machines_svc.occupy(db, await make_user(), printers[1].id, 60)
        await db.commit()
        await open_app(client, waiting)

        response = await client.post(f"/app/queue/join/{MachineKind.PRINTER}")

        assert response.status_code == 303
        assert await queue_svc.position_of(db, waiting.id) == 1

    async def test_app_pages_do_not_auto_return(self, client, printers, make_user):
        """Планшет общий и возвращается к доске сам; телефон — личный."""
        user = await make_user()
        await open_app(client, user)

        app_page = await client.get(f"/app/occupy/{printers[0].id}")
        kiosk_page = await client.get(f"/occupy/{printers[0].id}")

        assert "data-autoreturn" not in app_page.text
        assert "data-autoreturn" in kiosk_page.text

    async def test_kiosk_pages_have_no_external_script(self, client, printers):
        """Экран на стене должен читаться при мёртвом интернете."""
        response = await client.get("/")

        assert "telegram.org" not in response.text
