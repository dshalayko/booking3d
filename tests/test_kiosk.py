from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import assets, qr
from app.api.kiosk import duration_options
from app.api.render import templates
from app.config import Settings, settings
from app.enums import MachineKind, MachineStatus, ReservationStatus
from app.models import Machine, QueueEntry, Reservation
from app.services import auth
from app.services import machines as machines_svc
from app.services import queue as queue_svc
from app.services import reservations as reservations_svc
from app.services import schedule as schedule_svc


@pytest.fixture(autouse=True)
def clean_limiters():
    auth.pin_limiter._failures.clear()
    auth.pin_limiter._locked_until.clear()
    yield


async def enroll(client, room):
    """Зарегистрировать планшет в помещении — так его и настраивают на стене."""
    await client.get(
        f"/kiosk/enroll?secret={settings.kiosk_enroll_secret}&room={room.id}"
    )


class TestBoard:
    async def test_board_is_open_without_login(self, client, room, printers):
        response = await client.get(f"/room/{room.id}")

        assert response.status_code == 200
        assert "Свободно" in response.text
        assert "P2S #1" in response.text and "P2S #2" in response.text

    async def test_board_shows_who_is_printing(self, client, room, db, printers, make_user):
        user = await make_user(name="Иван")
        await machines_svc.occupy(db, user, printers[0].id, 120)
        await db.commit()

        response = await client.get(f"/room/{room.id}")

        assert "Занято" in response.text
        assert "Иван" in response.text

    async def test_board_shows_part_waiting_to_be_taken(
        self, client, room, db, printers, make_user
    ):
        user = await make_user()
        await machines_svc.occupy(db, user, printers[0].id, 60)
        await machines_svc.mark_done_wait(db, printers[0].id)
        await db.commit()

        response = await client.get(f"/room/{room.id}")

        assert "Заберите деталь" in response.text

    async def test_board_shows_queue_and_reservation(self, client, room, db, printers, make_user):
        owner = await make_user()
        other = await make_user()
        waiting = await make_user(name="Анна")
        await machines_svc.occupy(db, owner, printers[0].id, 60)
        await machines_svc.occupy(db, other, printers[1].id, 60)
        await queue_svc.join(db, waiting.id, room.id, MachineKind.PRINTER)
        await machines_svc.release(db, owner, printers[0].id)
        await db.commit()

        response = await client.get(f"/room/{room.id}")

        assert "Придержано" in response.text
        assert "Анна" in response.text

    async def test_partial_returns_only_the_fragment(self, client, room, printers):
        response = await client.get(f"/partials/board/{room.id}")

        assert response.status_code == 200
        assert "<html" not in response.text
        assert "P2S #1" in response.text

    async def test_hero_button_offers_queue_when_all_busy(
        self, client, room, db, printers, make_user
    ):
        for printer in printers:
            await machines_svc.occupy(db, await make_user(), printer.id, 60)
        await db.commit()

        response = await client.get(f"/room/{room.id}")

        assert "Встать в очередь" in response.text
        assert "все заняты" in response.text


class TestOccupy:
    async def test_form_shows_keypad(self, client, room, printers):
        await enroll(client, room)

        response = await client.get(f"/occupy/{printers[0].id}")

        assert "PIN" in response.text
        assert "до утра" in response.text or "12 ч" in response.text
        assert "Занять сейчас" in response.text
        assert 'type="radio" name="minutes"' in response.text
        assert 'type="submit" name="minutes"' not in response.text

    async def test_form_refuses_busy_printer_before_pin(
        self, client, room, db, printers, make_user
    ):
        """Иначе человек введёт PIN, выберет время и только тогда узнает об отказе."""
        machine_id = printers[0].id
        await machines_svc.occupy(db, await make_user(), machine_id, 60)
        await db.commit()
        await enroll(client, room)

        response = await client.get(f"/occupy/{machine_id}", headers={"accept": "text/html"})

        assert response.status_code == 409
        assert "уже занято" in response.text
        assert "keypad" not in response.text

    async def test_occupy_from_kiosk(self, client, room, db, printers, make_user):
        machine_id = printers[0].id
        await make_user(name="Пётр", pin="4242")
        await enroll(client, room)

        response = await client.post(
            f"/occupy/{machine_id}", data={"pin": "4242", "minutes": "120"}
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?flash=occupied"
        db.expire_all()
        assert (await db.get(Machine, machine_id)).status == MachineStatus.PRINTING

    async def test_second_printer_asks_pin_again(self, client, room, printers, make_user):
        """Занял и отошёл — следующий у планшета вводит свой PIN, а не чужой."""
        await make_user(pin="4242")
        await enroll(client, room)

        await client.post(f"/occupy/{printers[0].id}", data={"pin": "4242", "minutes": "60"})
        response = await client.get(f"/occupy/{printers[1].id}")

        assert "keypad" in response.text

    async def test_occupy_needs_kiosk_device(self, client, printers, make_user):
        await make_user(pin="4242")

        response = await client.post(
            f"/occupy/{printers[0].id}", data={"pin": "4242", "minutes": "60"}
        )

        assert response.status_code == 403

    async def test_kiosk_device_required_by_default(self):
        """Тестовый режим не должен однажды уехать в прод незамеченным.

        Проверяем дефолт в коде, а не `settings`: у того, кто прямо сейчас
        гоняет открытый доступ у себя, набор не должен краснеть.
        """
        assert Settings.model_fields["kiosk_open_access"].default is False

    async def test_wrong_pin_shows_error_screen(self, client, room, printers, make_user):
        await make_user(pin="4242")
        await enroll(client, room)

        response = await client.post(
            f"/occupy/{printers[0].id}",
            data={"pin": "0000", "minutes": "60"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 401
        assert "Неверный PIN" in response.text

    async def test_busy_printer_shows_error_screen(self, client, room, db, printers, make_user):
        await machines_svc.occupy(db, await make_user(), printers[0].id, 60)
        await db.commit()
        await make_user(pin="4242")
        await enroll(client, room)

        response = await client.post(
            f"/occupy/{printers[0].id}",
            data={"pin": "4242", "minutes": "60"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 409
        assert "уже занято" in response.text

    async def test_queue_jumping_is_refused_on_screen(self, client, room, db, printers, make_user):
        """Правило 7 глазами постороннего у киоска."""
        owner = await make_user()
        other = await make_user()
        waiting = await make_user()
        await machines_svc.occupy(db, owner, printers[0].id, 60)
        await machines_svc.occupy(db, other, printers[1].id, 60)
        await queue_svc.join(db, waiting.id, room.id, MachineKind.PRINTER)
        await machines_svc.release(db, owner, printers[0].id)
        await db.commit()
        await make_user(pin="4242")
        await enroll(client, room)

        response = await client.post(
            f"/occupy/{printers[0].id}",
            data={"pin": "4242", "minutes": "60"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 409
        assert "придержано" in response.text.lower()


class TestPinHelp:
    """Кнопка с QR под клавиатурой: адрес бота с планшета на стене не набрать."""

    @pytest.fixture
    def bot_named(self, monkeypatch: pytest.MonkeyPatch):
        """Имя бота в .env — так же, как оно попадает в шаблоны при старте."""
        monkeypatch.setattr(settings, "tg_bot_username", "@booking3d_bot")
        monkeypatch.setitem(templates.env.globals, "bot_username", qr.bot_username())
        monkeypatch.setitem(templates.env.globals, "bot_qr", qr.bot_qr_svg())

    def test_link_drops_the_at_sign(self, bot_named):
        assert qr.bot_url() == "https://t.me/booking3d_bot"

    def test_no_qr_without_username(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "tg_bot_username", "")

        assert qr.bot_url() == ""
        assert qr.bot_qr_svg() == ""

    async def test_keypad_shows_qr_button(self, client, room, printers, bot_named):
        await enroll(client, room)

        response = await client.get(f"/occupy/{printers[0].id}")

        assert "Как получить PIN" in response.text
        assert "@booking3d_bot" in response.text
        # Код уезжает разметкой, а не ссылкой на картинку: киоск рисуется и без
        # сети, а внешних запросов на его страницах нет ни одного.
        assert 'class="qr"' in response.text
        assert "http" not in response.text.split('class="qr"')[1].split("</svg>")[0]

    async def test_hint_stays_without_username(self, client, room, printers):
        await enroll(client, room)

        response = await client.get(f"/occupy/{printers[0].id}")

        assert "PIN выдаёт бот по команде /start" in response.text
        assert "data-pin-help" not in response.text


class TestOpenAccess:
    """`KIOSK_OPEN_ACCESS` — прогон цикла до того, как iPad повешен на стену."""

    @pytest.fixture(autouse=True)
    def open_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "kiosk_open_access", True)

    async def test_occupy_without_enrolled_device(self, client, db, printers, make_user):
        machine_id = printers[0].id
        await make_user(name="Пётр", pin="4242")

        response = await client.post(
            f"/occupy/{machine_id}", data={"pin": "4242", "minutes": "120"}
        )

        assert response.status_code == 303
        db.expire_all()
        assert (await db.get(Machine, machine_id)).status == MachineStatus.PRINTING

    async def test_queue_join_without_enrolled_device(self, client, room, db, printers, make_user):
        user = await make_user(pin="4242")
        await machines_svc.occupy(db, await make_user(), printers[0].id, 60)
        await machines_svc.occupy(db, await make_user(), printers[1].id, 60)
        await db.commit()

        response = await client.post(f"/queue/join/{room.id}/printer", data={"pin": "4242"})

        assert response.status_code == 303
        assert await queue_svc.position_of(db, user.id, room.id) == 1

    async def test_keypad_is_shown_without_enrolled_device(self, client, printers):
        response = await client.get(f"/occupy/{printers[0].id}")

        assert "keypad" in response.text

    async def test_wrong_pin_still_refused(self, client, printers, make_user):
        """Открытый доступ снимает привязку к устройству, но не проверку PIN."""
        await make_user(pin="4242")

        response = await client.post(
            f"/occupy/{printers[0].id}",
            data={"pin": "0000", "minutes": "60"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 401

    async def test_admin_stays_closed(self, client):
        """Режим про киоск: админку он не открывает."""
        response = await client.get("/admin")

        assert response.status_code == 403


class TestReleaseAndQueue:
    async def test_release_frees_printer(self, client, room, db, printers, make_user):
        machine_id = printers[0].id
        owner = await make_user(pin="4242")
        await machines_svc.occupy(db, owner, machine_id, 60)
        await db.commit()
        await enroll(client, room)

        response = await client.post(f"/release/{machine_id}", data={"pin": "4242"})

        assert response.headers["location"] == "/?flash=released"
        db.expire_all()
        assert (await db.get(Machine, machine_id)).status == MachineStatus.FREE

    async def test_stranger_cannot_release_while_printing(
        self, client, room, db, printers, make_user
    ):
        machine_id = printers[0].id
        owner = await make_user(pin="4242")
        await make_user(pin="7777")
        await machines_svc.occupy(db, owner, machine_id, 60)
        await db.commit()
        await enroll(client, room)

        response = await client.post(f"/release/{machine_id}", data={"pin": "7777"})

        assert response.status_code == 403
        db.expire_all()
        assert (await db.get(Machine, machine_id)).status == MachineStatus.PRINTING

    async def test_release_asks_pin_again(self, client, room, db, printers, make_user):
        """Освободил — принтер сразу занимает следующий, своим PIN."""
        owner = await make_user(pin="4242")
        await machines_svc.occupy(db, owner, printers[0].id, 60)
        await db.commit()
        await enroll(client, room)

        await client.post(f"/release/{printers[0].id}", data={"pin": "4242"})
        response = await client.get(f"/occupy/{printers[0].id}")

        assert "keypad" in response.text

    async def test_anyone_can_release(self, client, room, db, printers, make_user):
        """Правило 9: снял чужую деталь — освободил принтер."""
        machine_id = printers[0].id
        owner = await make_user()
        await make_user(pin="7777")
        await machines_svc.occupy(db, owner, machine_id, 60)
        await machines_svc.mark_done_wait(db, machine_id)
        await db.commit()
        await enroll(client, room)

        response = await client.post(f"/release/{machine_id}", data={"pin": "7777"})

        assert response.status_code == 303
        db.expire_all()
        assert (await db.get(Machine, machine_id)).status == MachineStatus.FREE

    async def test_join_and_leave_queue(self, client, room, db, printers, make_user):
        # Номер помещения запоминаем до `expire_all`: у отвязанной от сессии
        # строки обращение к полю полезло бы в базу вне асинхронного контекста.
        room_id = room.id
        user_id = (await make_user(pin="4242")).id
        for printer in printers:
            await machines_svc.occupy(db, await make_user(), printer.id, 60)
        await db.commit()
        await enroll(client, room)

        joined = await client.post(f"/queue/join/{room_id}/printer", data={"pin": "4242"})
        assert joined.headers["location"] == "/?flash=queued"
        db.expire_all()
        assert await queue_svc.position_of(db, user_id, room_id) == 1

        left = await client.post(f"/queue/leave/{room_id}", data={"pin": "4242"})
        assert left.headers["location"] == "/?flash=left"
        db.expire_all()
        assert await queue_svc.position_of(db, user_id, room_id) is None

    async def test_queue_asks_pin_again(self, client, room, db, printers, make_user):
        """Встал в очередь — выход из неё требует PIN заново, как и всё остальное."""
        await make_user(pin="4242")
        for printer in printers:
            await machines_svc.occupy(db, await make_user(), printer.id, 60)
        await db.commit()
        await enroll(client, room)

        await client.post(f"/queue/join/{room.id}/printer", data={"pin": "4242"})
        response = await client.get(f"/queue/leave/{room.id}")

        assert "keypad" in response.text

    async def test_flash_message_is_shown(self, client, room, printers):
        response = await client.get(f"/room/{room.id}?flash=released")

        assert "Освобождено" in response.text

    async def test_unknown_flash_is_ignored(self, client, printers):
        response = await client.get("/?flash=<script>")

        assert "<script>" not in response.text


class TestScheduleScreen:
    """Расписание и брони с планшета: те же экраны, что и в Mini App."""

    async def test_schedule_shows_days_and_working_hours(self, client, room, printers):
        """В сетке только рабочие часы: ночь никому не нужна, а 24 строки на
        телефоне — это экран, который надо листать."""
        response = await client.get(f"/schedule/{room.id}/{MachineKind.PRINTER}")

        assert response.status_code == 200
        assert "сегодня" in response.text
        assert "P2S #1" in response.text and "P2S #2" in response.text
        # Последний слот начинается в 19:00 и кончается ровно к закрытию.
        assert "08:00" in response.text and "19:00" in response.text
        assert "03:00" not in response.text
        assert "Открыто 08:00–20:00" in response.text

    async def test_unknown_kind_is_refused(self, client, room, printers):
        response = await client.get(f"/schedule/{room.id}/toaster", headers={"accept": "text/html"})

        assert response.status_code == 400

    async def test_broken_date_falls_back_to_today(self, client, room, printers):
        """Расписание — экран для чтения: отказ на опечатке в ссылке никого не
        защитит, а человека у стены остановит."""
        response = await client.get(f"/schedule/{room.id}/{MachineKind.PRINTER}?date=не-дата")

        assert response.status_code == 200

    async def test_board_links_to_schedule(self, client, room, printers):
        response = await client.get(f"/room/{room.id}")

        assert f"/schedule/{room.id}/{MachineKind.PRINTER}" in response.text

    async def test_booked_hour_is_shown_on_the_board(self, client, room, db, printers, make_user):
        user = await make_user(name="Анна")
        now = datetime.now(UTC)
        start = schedule_svc.align(now) + timedelta(days=1)
        await reservations_svc.book(db, user, printers[0].id, start, 120)
        await db.commit()

        response = await client.get(f"/room/{room.id}")

        assert "бронь с" in response.text


class TestBookScreen:
    async def test_form_asks_pin_and_duration(self, client, room, printers, work_slot):
        await enroll(client, room)

        response = await client.get(
            f"/book/{printers[0].id}", params={"start": work_slot().isoformat()}
        )

        assert response.status_code == 200
        assert "PIN" in response.text
        assert "Забронировать" in response.text

    async def test_form_refuses_past_hour_before_pin(self, client, room, printers):
        await enroll(client, room)
        past = schedule_svc.align(datetime.now(UTC)) - timedelta(days=1)

        response = await client.get(
            f"/book/{printers[0].id}",
            params={"start": past.isoformat()},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 400
        assert "keypad" not in response.text

    async def test_form_refuses_taken_hour_before_pin(
        self, client, room, db, printers, make_user, work_slot
    ):
        user = await make_user()
        start = work_slot()
        await reservations_svc.book(db, user, printers[0].id, start, 60)
        await db.commit()
        await enroll(client, room)

        response = await client.get(
            f"/book/{printers[0].id}",
            params={"start": start.isoformat()},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 409
        assert "keypad" not in response.text

    async def test_booking_from_kiosk(self, client, room, db, printers, make_user, work_slot):
        await make_user(name="Пётр", pin="4242")
        start = work_slot()
        await enroll(client, room)

        response = await client.post(
            f"/book/{printers[0].id}",
            data={"pin": "4242", "start": start.isoformat(), "minutes": "120"},
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?flash=booked"
        booking = await db.scalar(select(Reservation))
        assert booking.starts_at == start
        assert booking.status == ReservationStatus.BOOKED

    async def test_booking_without_pin_is_refused(
        self, client, room, db, printers, make_user, work_slot
    ):
        await make_user(pin="4242")
        await enroll(client, room)

        response = await client.post(
            f"/book/{printers[0].id}",
            data={"pin": "", "start": work_slot().isoformat(), "minutes": "60"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 401
        assert await db.scalar(select(Reservation)) is None

    async def test_booking_from_a_laptop_is_refused(
        self, client, db, printers, make_user, work_slot
    ):
        """Правило 11: PIN вводится только на планшете."""
        await make_user(pin="4242")

        response = await client.post(
            f"/book/{printers[0].id}",
            data={"pin": "4242", "start": work_slot().isoformat(), "minutes": "60"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 403
        assert await db.scalar(select(Reservation)) is None

    async def test_owner_cancels_from_kiosk(self, client, room, db, printers, make_user, work_slot):
        user = await make_user(pin="4242")
        booking = await reservations_svc.book(
            db, user, printers[0].id, work_slot(), 60
        )
        await db.commit()
        await enroll(client, room)

        response = await client.post(
            f"/booking/{booking.reservation_id}/cancel", data={"pin": "4242"}
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/?flash=booking_cancelled"
        db.expire_all()
        stored = await db.get(Reservation, booking.reservation_id)
        assert stored.status == ReservationStatus.CANCELLED

    async def test_stranger_cannot_cancel_from_kiosk(
        self, client, room, db, printers, make_user, work_slot
    ):
        owner = await make_user(pin="4242")
        await make_user(pin="1111")
        booking = await reservations_svc.book(
            db, owner, printers[0].id, work_slot(), 60
        )
        await db.commit()
        await enroll(client, room)

        response = await client.post(
            f"/booking/{booking.reservation_id}/cancel",
            data={"pin": "1111"},
            headers={"accept": "text/html"},
        )

        assert response.status_code == 403
        db.expire_all()
        stored = await db.get(Reservation, booking.reservation_id)
        assert stored.status == ReservationStatus.BOOKED


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


class TestSelfUpdate:
    """Планшет на стене должен обновляться после деплоя сам, а не пешком."""

    async def test_page_carries_the_version_it_was_built_with(self, client, room):
        response = await client.get(f"/room/{room.id}")

        assert f'data-version="{assets.VERSION}"' in response.text

    async def test_poll_answers_with_the_current_version(self, client, room, printers):
        """Именно по этому заголовку страница и узнаёт, что сервер обновился."""
        response = await client.get(f"/partials/board/{room.id}")

        assert response.headers["X-App-Version"] == assets.VERSION

    def test_version_follows_the_files_not_the_restart(self, tmp_path, monkeypatch):
        page = tmp_path / "app.css"
        page.write_text("body { color: red }")
        monkeypatch.setattr(assets, "WATCHED", (tmp_path,))

        before = assets.digest()
        assert assets.digest() == before  # перезапуск без правок экраны не дёргает

        page.write_text("body { color: blue }")
        assert assets.digest() != before


class TestDurations:
    def test_night_option_counts_until_morning(self):
        evening = datetime(2026, 8, 10, 19, 0, tzinfo=UTC)  # 22:00 в Никосии

        options = duration_options(evening)

        night = [option for option in options if option.label == "до утра"][0]
        assert night.minutes == 11 * 60  # с 22:00 до 09:00

    def test_night_option_hidden_right_before_morning(self):
        early = datetime(2026, 8, 10, 5, 55, tzinfo=UTC)  # 08:55 в Никосии

        labels = [option.label for option in duration_options(early)]

        assert "до утра" not in labels

    def test_options_stop_at_the_next_booking(self):
        """Кнопка, ведущая к отказу, хуже отсутствующей: PIN уже введён."""
        noon = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)

        options = duration_options(noon, limit_minutes=150)

        assert [option.minutes for option in options] == [60, 120]


class TestKindsOnTheWall:
    """Секция на тип: у принтеров и гравировщиков свои очереди."""

    async def test_board_has_a_section_per_kind(self, client, room, printers, engravers):
        response = await client.get(f"/room/{room.id}")

        assert "Принтеры" in response.text and "Гравировщики" in response.text
        assert "Гравёр #1" in response.text

    async def test_each_section_has_its_own_queue_button(self, client, room, printers, engravers):
        response = await client.get(f"/room/{room.id}")

        assert f"/queue/join/{room.id}/printer" in response.text
        assert f"/queue/join/{room.id}/engraver" in response.text

    async def test_busy_engraver_says_it_engraves(self, client, room, db, engravers, make_user):
        await machines_svc.occupy(db, await make_user(), engravers[0].id, 60)
        await db.commit()

        response = await client.get(f"/room/{room.id}")

        assert "Занято" in response.text

    async def test_empty_park_explains_itself(self, client, room):
        """Первый запуск: парк пустой, и экран должен сказать, куда идти."""
        response = await client.get(f"/room/{room.id}")

        assert response.status_code == 200
        assert "админк" in response.text

    async def test_join_the_engraver_line_from_the_kiosk(
        self, client, room, db, printers, engravers, make_user
    ):
        person_id = (await make_user(pin="4242")).id
        await machines_svc.occupy(db, await make_user(), engravers[0].id, 60)
        await db.commit()
        await enroll(client, room)

        response = await client.post(f"/queue/join/{room.id}/engraver", data={"pin": "4242"})

        assert response.status_code == 303
        db.expire_all()
        entry = await db.scalar(select(QueueEntry).where(QueueEntry.user_id == person_id))
        assert entry.kind == MachineKind.ENGRAVER

    async def test_unknown_kind_is_refused(self, client, room, printers):
        await enroll(client, room)

        response = await client.get(f"/queue/join/{room.id}/laser", headers={"accept": "text/html"})

        assert response.status_code == 400
