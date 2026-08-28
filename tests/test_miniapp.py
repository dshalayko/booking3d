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
import html
import json
import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from app import texts as t
from app.bot import notify
from app.config import settings
from app.enums import MachineKind, MachineStatus, ReservationStatus
from app.models import FeedbackRequest, Machine, Reservation
from app.services import booking_policy, telegram
from app.services import machines as machines_svc
from app.services import reservations as reservations_svc
from app.services import schedule as schedule_svc
from app.services.errors import BadInitData

BOT_TOKEN = "123456:test-token"


@pytest.fixture
def outbox() -> list[tuple[int, str]]:
    sent: list[tuple[int, str]] = []

    async def sender(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    notify.set_sender(sender)
    yield sent
    notify.set_sender(None)


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
        assert "data-tg-close" in response.text
        assert t.UI["app_close"] in response.text

        script = await client.get("/static/app.js")
        assert 'tg.close()' in script.text

    async def test_valid_open_sets_session_and_shows_board(self, client, room, printers, make_user):
        user = await make_user()

        response = await client.post(
            "/app/session", data={"init_data": _sign(user.tg_chat_id), "next": "/app/"}
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/app/"
        # Своего у человека пока ничего нет, и главный экран — доска оборудования.
        board = await client.get("/app/")
        assert room.name not in board.text
        assert printers[0].name in board.text
        assert "board-page miniapp-page" in board.text

        bookings = await client.get("/app/my")
        assert (
            f'class="btn btn-small btn-wide" '
            f'href="/app/schedule/{room.id}/{MachineKind.PRINTER}"' in bookings.text
        )

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

    async def test_status_without_session_opens_bootstrap(self, client):
        response = await client.get("/app/status")

        assert response.status_code == 200
        assert 'value="/app/status"' in response.text


class TestFeedback:
    async def test_button_and_form_are_available_to_signed_in_user(
        self, client, printers, make_user
    ):
        person = await make_user(name="d_shalayko")
        await open_app(client, person)

        home = await client.get("/app/")
        assert 'href="/app/feedback"' in home.text
        assert t.UI["feedback_button"] in home.text

        form = await client.get("/app/feedback")
        assert form.status_code == 200
        assert 'name="username"' in form.text
        assert 'value="d_shalayko"' in form.text
        assert 'name="message"' in form.text

    async def test_submission_is_saved_and_redirects_home(
        self, client, db, printers, make_user
    ):
        person = await make_user(name="d_shalayko")
        await open_app(client, person)

        response = await client.post(
            "/app/feedback",
            data={"username": "  Dan  ", "message": "  Add another printer  "},
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/app/?flash=feedback_sent"
        stored = await db.scalar(select(FeedbackRequest))
        assert stored is not None
        assert stored.user_id == person.id
        assert stored.username == "Dan"
        assert stored.message == "Add another printer"

    async def test_empty_feedback_is_refused(self, client, db, printers, make_user):
        person = await make_user()
        await open_app(client, person)

        response = await client.post(
            "/app/feedback",
            data={"username": person.name, "message": "   "},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 400
        assert await db.scalar(select(FeedbackRequest)) is None

    async def test_form_without_session_bootstraps_and_post_is_refused(self, client):
        form = await client.get("/app/feedback")
        assert form.status_code == 200
        assert 'value="/app/feedback"' in form.text

        action = await client.post(
            "/app/feedback", data={"username": "x", "message": "test"}
        )
        assert action.status_code == 401


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

    async def test_sign_in_without_signature(
        self, client, room, db, printers, open_access, make_user
    ):
        person = await make_user()

        response = await client.post("/app/session", data={"as_user_id": person.id})

        assert response.status_code == 303
        board = await client.get("/app/")
        assert room.name not in board.text
        assert printers[0].name in board.text

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

    async def test_back_from_the_form_returns_to_the_schedule(
        self, client, room, printers, make_user
    ):
        """Та же ссылка, но с префиксом /app: помещение в ней тоже обязано быть."""
        user = await make_user()
        await open_app(client, user)

        form = await client.get(
            f"/app/book/{printers[0].id}", params={"start": tomorrow_at().isoformat()}
        )
        back = re.search(r'href="([^"]*/schedule/[^"]+)"', form.text).group(1)
        schedule = await client.get(html.unescape(back))

        assert back.startswith(f"/app/schedule/{room.id}/{MachineKind.PRINTER}")
        assert schedule.status_code == 200

    async def test_booking_form_can_switch_the_selected_printer(
        self, client, printers, make_user
    ):
        user = await make_user()
        await open_app(client, user)
        start = tomorrow_at()

        first = await client.get(
            f"/app/book/{printers[0].id}", params={"start": start.isoformat()}
        )
        switched = await client.get(
            "/app/choose-machine",
            params={"machine_id": printers[1].id, "start": start.isoformat()},
        )

        assert t.UI["book_machine_label"] in first.text
        assert printers[0].name in first.text and printers[1].name in first.text
        assert f'value="{printers[0].id}"' in first.text
        assert f'action="/app/book/{printers[1].id}"' in switched.text
        assert t.UI["book_heading"].format(machine=printers[1].name) in switched.text

    async def test_printer_booked_at_that_time_is_disabled_in_picker(
        self, client, db, printers, make_user
    ):
        start = tomorrow_at()
        other = await make_user(name="Другой")
        await reservations_svc.book(db, other, printers[1].id, start, 60)
        await db.commit()
        user = await make_user()
        await open_app(client, user)

        form = await client.get(
            f"/app/book/{printers[0].id}", params={"start": start.isoformat()}
        )

        option = re.search(
            rf'<option value="{printers[1].id}"(?P<attrs>[^>]*)>', form.text
        )
        assert option is not None
        assert "disabled" in option.group("attrs")
        assert t.UI["book_machine_unavailable"] in form.text

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

    async def test_booking_lands_on_my_bookings(self, client, printers, make_user):
        """Забронировав, человек должен увидеть, что время записано, и какое.

        На стене такого экрана нет — список чужих брон там не место, и планшет
        по-прежнему возвращается к доске.
        """
        user = await make_user()
        await open_app(client, user)

        response = await client.post(
            f"/app/book/{printers[0].id}",
            data={"start": tomorrow_at().isoformat(), "minutes": "60"},
        )

        assert response.headers["location"] == "/app/?flash=booked"
        listing = await client.get("/app/", params={"flash": "booked"})
        assert "P2S #1" in listing.text
        assert "my-page" in listing.text
        assert "banner-ok" in listing.text

    async def test_started_booking_stays_visible_until_work_is_released(
        self, client, db, room, printers, make_user
    ):
        user = await make_user()
        start = tomorrow_at()
        await reservations_svc.book(db, user, printers[0].id, start, 60)
        await machines_svc.occupy(db, user, printers[0].id, 60, now=start)
        await db.commit()
        await open_app(client, user)

        listing = await client.get("/app/my")
        mine = await client.get("/app/")

        assert "P2S #1" in listing.text
        assert t.UI["my_in_progress"] in listing.text
        assert t.UI["my_current"] not in listing.text
        assert f'/app/release/{printers[0].id}' in listing.text
        assert f'/app/schedule/{room.id}/{MachineKind.PRINTER}' not in mine.text
        assert t.UI["board_schedule_cta"] not in mine.text

        await machines_svc.release(db, user, printers[0].id, now=start + timedelta(hours=1))
        await db.commit()
        finished = await client.get("/app/my")

        assert t.UI["my_in_progress"] not in finished.text
        assert f'/app/schedule/{room.id}/{MachineKind.PRINTER}' in finished.text

    async def test_take_now_is_visible_in_my_bookings(
        self, client, db, room, printers, make_user
    ):
        user = await make_user()
        await machines_svc.occupy(db, user, printers[0].id, 120)
        await db.commit()
        await open_app(client, user)

        listing = await client.get("/app/my")

        assert printers[0].name in listing.text
        assert room.name in listing.text
        assert t.UI["my_current"] in listing.text
        assert f'href="/app/release/{printers[0].id}"' in listing.text
        assert 'href="/app/status"' in listing.text
        assert t.UI["my_blocked"] not in listing.text

    async def test_extended_policy_shows_all_current_jobs(
        self, client, db, printers, make_user
    ):
        user = await make_user()
        await booking_policy.save(db, True)
        await machines_svc.occupy(db, user, printers[0].id, 120)
        await machines_svc.occupy(db, user, printers[1].id, 120)
        await db.commit()
        await open_app(client, user)

        listing = await client.get("/app/my")

        assert printers[0].name in listing.text
        assert printers[1].name in listing.text

    async def test_active_booking_hides_new_booking_links(
        self, client, db, room, printers, make_user
    ):
        user = await make_user()
        await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()
        await open_app(client, user)

        listing = await client.get("/app/my")
        schedule = await client.get(
            f"/app/schedule/{room.id}/{MachineKind.PRINTER}"
        )

        assert f'/app/schedule/{room.id}/{MachineKind.PRINTER}' not in listing.text
        assert schedule.status_code == 303
        assert schedule.headers["location"] == "/app/"

    async def test_active_booking_turns_app_into_own_booking_screen(
        self, client, db, room, printers, engravers, make_user
    ):
        user = await make_user()
        await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()
        await open_app(client, user)

        root = await client.get("/app/")

        assert root.status_code == 200
        assert t.UI["my_heading"] in root.text
        assert f'>{t.UI["back"]}</a>' not in root.text
        assert printers[0].name in root.text
        assert printers[1].name not in root.text
        assert engravers[0].name not in root.text
        assert "/app/schedule/" not in root.text
        assert 'href="/app/status"' in root.text

    async def test_active_booking_can_open_read_only_status_and_return(
        self, client, db, room, printers, engravers, make_user
    ):
        user = await make_user()
        other = await make_user(name="Другой")
        await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await machines_svc.occupy(db, other, printers[1].id, 60)
        await db.commit()
        await open_app(client, user)

        status = await client.get("/app/status")
        partial = await client.get("/app/partials/status")

        assert status.status_code == 200
        assert "status-page" in status.text
        assert printers[0].name in status.text
        assert printers[1].name in status.text
        assert engravers[0].name in status.text
        assert 'href="/app/"' in status.text
        assert 'data-poll="/app/partials/status"' in status.text
        assert "/app/occupy/" not in status.text
        assert "/app/release/" not in status.text
        assert "/app/schedule/" not in status.text
        assert printers[1].name in partial.text
        assert "/app/release/" not in partial.text

    async def test_active_booking_blocks_all_direct_booking_routes(
        self, client, db, room, printers, make_user
    ):
        user = await make_user()
        await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()
        await open_app(client, user)

        responses = [
            await client.get(f"/app/room/{room.id}"),
            await client.get(f"/app/schedule/{room.id}/{MachineKind.PRINTER}"),
            await client.get(
                f"/app/book/{printers[1].id}",
                params={"start": tomorrow_at(hour=12).isoformat()},
            ),
            await client.get(
                "/app/choose-machine",
                params={
                    "machine_id": printers[1].id,
                    "start": tomorrow_at(hour=12).isoformat(),
                },
            ),
            await client.post(
                f"/app/book/{printers[1].id}",
                data={"start": tomorrow_at(hour=12).isoformat(), "minutes": "60"},
            ),
        ]

        assert all(response.status_code == 303 for response in responses)
        assert all(response.headers["location"] == "/app/" for response in responses)
        assert len(await reservations_svc.of_user(db, user.id)) == 1

    async def test_blocked_booking_is_explained_in_the_bot(
        self, client, db, printers, make_user, outbox
    ):
        """Возврат на главный экран молчит — причину человек читает в боте."""
        user = await make_user()
        await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()
        await open_app(client, user)

        response = await client.post(
            f"/app/book/{printers[1].id}",
            data={"start": tomorrow_at(hour=12).isoformat(), "minutes": "60"},
        )

        assert response.headers["location"] == "/app/"
        assert outbox == [(user.tg_chat_id, outbox[0][1])]
        assert "уже есть активная бронь" in outbox[0][1]

    async def test_extended_policy_keeps_more_booking_routes_open(
        self, client, db, room, printers, engravers, make_user
    ):
        user = await make_user()
        await booking_policy.save(db, True)
        await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()
        await open_app(client, user)

        mine = await client.get("/app/my")
        schedule = await client.get(
            f"/app/schedule/{room.id}/{MachineKind.PRINTER}"
        )
        second = await client.post(
            f"/app/book/{printers[1].id}",
            data={"start": tomorrow_at(hour=12).isoformat(), "minutes": "60"},
        )

        assert schedule.status_code == 200
        assert second.status_code == 303
        assert second.headers["location"].startswith("/app/")
        assert f"/app/schedule/{room.id}/{MachineKind.PRINTER}" in mine.text
        assert f"/app/schedule/{room.id}/{MachineKind.ENGRAVER}" in mine.text

    async def test_cancelling_returns_to_the_main_screen(
        self, client, db, printers, make_user
    ):
        """И закрывает бронь в базе, а не только на экране.

        Отмена, которая доехала до экрана, но не до строки, — это следующий
        отказ «у вас уже есть бронь» на ровном месте, и человеку неоткуда узнать,
        откуда она взялась.
        """
        user = await make_user()
        # Номер помещения и человека — до `expire_all`: у отвязанной от сессии
        # строки обращение к полю полезло бы в базу вне асинхронного контекста.
        user_id = user.id
        booking = await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()
        await open_app(client, user)

        response = await client.post(f"/app/booking/{booking.reservation_id}/cancel")

        assert response.headers["location"] == "/app/?flash=booking_cancelled"
        db.expire_all()
        stored = await db.get(Reservation, booking.reservation_id)
        assert stored.status == ReservationStatus.CANCELLED
        assert stored.resolved_at is not None
        # И лимит правила 13 свободен сразу же, без всякой паузы.
        assert await reservations_svc.active_of_user(db, user_id) is None

    async def test_schedule_redirects_to_my_booking(self, client, room, db, printers, make_user):
        user = await make_user(name="Аня")
        await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()
        await open_app(client, user)

        response = await client.get(
            f"/app/schedule/{room.id}/{MachineKind.PRINTER}",
            params={"date": schedule_svc.day_of(tomorrow_at()).isoformat()},
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/app/"

    async def test_kiosk_schedule_highlights_nothing(self, client, room, db, printers, make_user):
        """На стене неизвестно, кто смотрит, — своих брон там нет."""
        user = await make_user(name="Аня")
        await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()

        response = await client.get(
            f"/schedule/{room.id}/{MachineKind.PRINTER}",
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

    async def test_app_pages_do_not_auto_return(self, client, printers, make_user):
        """Планшет общий и возвращается к доске сам; телефон — личный."""
        user = await make_user()
        await open_app(client, user)

        app_page = await client.get(f"/app/occupy/{printers[0].id}")
        kiosk_page = await client.get(f"/occupy/{printers[0].id}")

        assert "data-autoreturn" not in app_page.text
        assert "data-autoreturn" in kiosk_page.text

    async def test_cancel_screen_does_not_offer_a_second_cancel(
        self, client, db, printers, make_user
    ):
        """«Отмена» рядом с «Отменить бронь» нажимают вместо неё — и не отменяют.

        Уход с экрана и отмена брони — разные вещи, и называться одинаково они не
        могут: человек жмёт верхнюю кнопку, бронь остаётся, а следом он упирается
        в «у вас уже есть бронь» и не понимает, откуда она. PIN там тоже не
        упоминается: клавиатуры на этом экране нет, и упоминание читается как
        «чего-то не хватает, поэтому и не отменяется».
        """
        user = await make_user()
        booking = await reservations_svc.book(db, user, printers[0].id, tomorrow_at(), 60)
        await db.commit()
        await open_app(client, user)

        page = (await client.get(f"/app/booking/{booking.reservation_id}/cancel")).text

        assert "Отменить бронь" in page
        assert "Назад" in page
        assert "Отмена" not in page
        assert "PIN" not in page

    async def test_error_screen_stays_in_the_app(self, client, db, printers, make_user):
        """Иначе выход с экрана ошибки — перезапуск Telegram.

        Кнопка «Понятно» вела на `/`, то есть на доску киоска с клавиатурой PIN:
        телефону её всё равно не примут (правило 11), а обратно в приложение
        оттуда нет ни одной ссылки. Авто-возврат туда же через минуту — тем более.
        """
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
        assert 'href="/app/"' in response.text
        assert 'href="/"' not in response.text
        assert "data-autoreturn" not in response.text

    async def test_kiosk_error_screen_still_goes_to_the_board(self, client, printers):
        """А на стене — наоборот: экран ошибки обязан сам вернуться к доске."""
        machines_id = printers[0].id

        response = await client.get(
            f"/book/{machines_id}", params={"start": "не время"}, headers={"accept": "text/html"}
        )

        assert response.status_code == 400
        assert 'href="/"' in response.text
        assert "data-autoreturn" in response.text

    async def test_kiosk_pages_have_no_external_script(self, client, room, printers):
        """Экран на стене должен читаться при мёртвом интернете."""
        response = await client.get(f"/room/{room.id}")

        assert "telegram.org" not in response.text


class TestUnifiedMyScreen:
    """Текущая работа и календарная бронь используют один экран «Моё»."""

    async def test_only_my_machine_is_shown(
        self, client, db, room, printers, engravers, make_user
    ):
        user = await make_user()
        await machines_svc.occupy(db, user, printers[0].id, 60)
        await db.commit()
        await open_app(client, user)

        page = (await client.get("/app/")).text

        assert "my-page miniapp-page" in page
        assert "P2S #1" in page
        assert room.name in page
        assert "P2S #2" not in page
        assert "Гравёр #1" not in page

    async def test_my_machine_keeps_its_action(self, client, db, printers, make_user):
        """Главный экран даёт завершить работу и открыть статусы."""
        user = await make_user()
        await machines_svc.occupy(db, user, printers[0].id, 60)
        await db.commit()
        await open_app(client, user)

        page = (await client.get("/app/")).text

        assert f'href="/app/release/{printers[0].id}"' in page
        assert "focused-page" not in page
        assert t.UI["my_heading"] in page
        assert f'href="/app/occupy/{printers[1].id}"' not in page
        assert 'href="/app/status"' in page

    async def test_with_nothing_of_mine_the_first_room_opens(
        self, client, room, printers, make_user
    ):
        """Сжимать нечего — вместо пустого экрана доска помещения.

        Списка «все помещения» в системе нет: у каждой комнаты свой адрес.
        """
        user = await make_user()
        await open_app(client, user)

        page = (await client.get("/app/")).text

        assert room.name not in page
        assert "P2S #1" in page and "P2S #2" in page

    async def test_room_board_opens_the_whole_room(self, client, room, db, printers, make_user):
        user = await make_user()
        await machines_svc.occupy(db, user, printers[0].id, 60)
        await db.commit()
        await open_app(client, user)

        page = (await client.get(f"/app/room/{room.id}")).text

        assert "P2S #1" in page and "P2S #2" in page
        # Опрос обязан спрашивать ту же доску, что открыта.
        assert f'data-poll="/app/partials/board/{room.id}"' in page

    async def test_direct_my_link_uses_the_same_screen(
        self, client, room, db, printers, make_user
    ):
        user = await make_user()
        await machines_svc.occupy(db, user, printers[0].id, 60)
        await db.commit()
        await open_app(client, user)

        root = (await client.get("/app/")).text
        direct = (await client.get("/app/my")).text

        assert "my-page miniapp-page" in root
        assert "my-page miniapp-page" in direct
        assert room.name in root and room.name in direct
        assert t.UI["app_view_statuses"] in root and t.UI["app_view_statuses"] in direct

    async def test_kiosk_board_is_never_narrowed(self, client, room, db, printers, make_user):
        """На стене неизвестно, кто смотрит, и парк помещения нужен весь."""
        user = await make_user()
        await machines_svc.occupy(db, user, printers[0].id, 60)
        await db.commit()
        await open_app(client, user)

        page = (await client.get(f"/room/{room.id}")).text

        assert "P2S #1" in page
        assert "P2S #2" in page
