import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.bot import commands, notify, texts
from app.config import settings
from app.enums import MachineKind, MachineStatus
from app.models import Machine, User
from app.services import auth
from app.services import machines as machines_svc
from app.services import queue as queue_svc
from app.services import reservations as reservations_svc
from app.services.errors import AuthFailed

CHAT = 5001
OTHER_CHAT = 5002


@pytest.fixture
def outbox() -> list[tuple[int, str]]:
    """Подменяем отправку списком: домен от Telegram не зависит."""
    sent: list[tuple[int, str]] = []

    async def sender(chat_id: int, text: str) -> None:
        sent.append((chat_id, text))

    notify.set_sender(sender)
    yield sent
    notify.set_sender(None)


class TestRegistration:
    async def test_start_asks_for_login_without_issuing_pin(self, db):
        answer = await commands.start(db, CHAT)

        assert "n_username" in answer
        assert await user_of(db, CHAT) is None

    async def test_login_finishes_registration(self, db):
        await commands.start(db, CHAT)

        answer = await commands.register(db, CHAT, "i_petrov")

        assert "i_petrov" in answer
        user = await auth.user_by_pin(db, _pin_from(answer))
        assert user.tg_chat_id == CHAT
        assert user.name == "i_petrov"

    @pytest.mark.parametrize(
        "value", ["Иван Петров", "petrov", "i petrov", "_petrov", "i_", "и_петров", ""]
    )
    async def test_junk_instead_of_login_gets_no_pin(self, db, value):
        answer = await commands.register(db, CHAT, value)

        assert "не похоже" in answer
        assert await user_of(db, CHAT) is None

    @pytest.mark.parametrize(
        ("typed", "stored"),
        [("@i_petrov", "i_petrov"), ("  I_Petrov  ", "i_petrov"), ("ab_petrov-2", "ab_petrov-2")],
    )
    async def test_login_is_normalized(self, db, typed, stored):
        await commands.register(db, CHAT, typed)

        assert (await user_of(db, CHAT)).name == stored

    async def test_login_taken_by_another_telegram(self, db):
        await commands.register(db, CHAT, "i_petrov")

        answer = await commands.register(db, OTHER_CHAT, "I_PETROV")

        assert "уже занят" in answer
        assert await user_of(db, OTHER_CHAT) is None

    async def test_unregistered_text_is_read_as_login(self, db):
        answer = await commands.text_message(db, CHAT, "i_petrov")

        assert _pin_from(answer)
        assert (await user_of(db, CHAT)).name == "i_petrov"

    async def test_unregistered_command_is_not_read_as_login(self, db):
        answer = await commands.text_message(db, CHAT, "/whatever")

        assert "start" in answer
        assert await user_of(db, CHAT) is None

    async def test_registered_text_gets_help(self, db):
        await register(db)

        assert await commands.text_message(db, CHAT, "привет") == texts.HELP

    async def test_start_twice_does_not_reset_pin(self, db):
        pin = _pin_from(await register(db))

        second = await commands.start(db, CHAT)

        assert "уже зарегистрированы" in second
        assert (await auth.user_by_pin(db, pin)).tg_chat_id == CHAT

    async def test_new_pin_kills_the_old_one(self, db):
        old = _pin_from(await register(db))

        new = _pin_from(await commands.new_pin(db, CHAT))

        assert new != old
        assert (await auth.user_by_pin(db, new)).tg_chat_id == CHAT
        with pytest.raises(AuthFailed):
            await auth.user_by_pin(db, old)

    @pytest.mark.parametrize(
        "command", [commands.my, commands.queue_join, commands.free, commands.new_pin]
    )
    async def test_commands_ask_unknown_people_to_register(self, db, command):
        assert "start" in await command(db, 999999)


class TestStatus:
    async def test_status_lists_free_printers(self, db, printers):
        answer = await commands.status(db)

        assert "P2S #1" in answer and "свободен" in answer
        assert "Очередь пуста" in answer

    async def test_status_shows_who_prints_and_how_long(self, db, printers, make_user):
        user = await make_user(name="Иван П.")
        await machines_svc.occupy(db, user, printers[0].id, 120)

        answer = await commands.status(db)

        assert "печатает" in answer
        assert "Иван П." in answer
        assert "осталось ~2 ч" in answer

    async def test_status_shows_queue_with_invitation(self, db, printers, make_user):
        owner = await make_user()
        other = await make_user()
        waiting = await make_user(name="Анна")
        await machines_svc.occupy(db, owner, printers[0].id, 60)
        await machines_svc.occupy(db, other, printers[1].id, 60)
        await queue_svc.join(db, waiting.id, MachineKind.PRINTER)
        await machines_svc.release(db, owner, printers[0].id)

        answer = await commands.status(db)

        assert "придержан за Анна" in answer
        assert "(приглашён)" in answer

    async def test_status_shows_broken_printer_with_note(self, db, printers, make_user):
        admin = await make_user(is_admin=True)
        await machines_svc.set_broken(db, admin, printers[0].id, note="полетел хотэнд")

        answer = await commands.status(db)

        assert "в обслуживании" in answer
        assert "полетел хотэнд" in answer


class TestMy:
    async def test_nothing_is_going_on(self, db):
        await register(db)

        assert "ничего не числится" in await commands.my(db, CHAT)

    async def test_shows_active_print(self, db, printers):
        await register(db)
        user = await user_of(db, CHAT)
        await machines_svc.occupy(db, user, printers[0].id, 240)

        answer = await commands.my(db, CHAT)

        assert "P2S #1" in answer
        assert "осталось ~4 ч" in answer

    async def test_shows_queue_position(self, db, printers, make_user):
        await register(db)
        for printer in printers:
            await machines_svc.occupy(db, await make_user(), printer.id, 60)
        await commands.queue_join(db, CHAT)

        assert "номер 1" in await commands.my(db, CHAT)

    async def test_shows_offer_deadline(self, db, printers, make_user):
        await register(db)
        owner = await make_user()
        other = await make_user()
        await machines_svc.occupy(db, owner, printers[0].id, 60)
        await machines_svc.occupy(db, other, printers[1].id, 60)
        await commands.queue_join(db, CHAT)
        await machines_svc.release(db, owner, printers[0].id)
        await db.commit()

        answer = await commands.my(db, CHAT)

        assert "предложен" in answer
        assert "P2S #1" in answer


    async def test_shows_booking(self, db, printers):
        await register(db)
        user = await user_of(db, CHAT)
        # Рабочий час, а не «текущий час завтра»: бронировать можно только
        # часы работы мастерской, и ночной прогон получал бы отказ.
        start = (datetime.now(settings.zone) + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        await reservations_svc.book(db, user, printers[0].id, start, 120)

        answer = await commands.my(db, CHAT)

        assert "Бронь" in answer
        assert "P2S #1" in answer


class TestBookCommand:
    async def test_invites_to_the_app(self, db, monkeypatch):
        await register(db)
        monkeypatch.setattr(settings, "public_base_url", "https://booking.example")

        invite = await commands.book(db, CHAT)

        assert invite.url == "https://booking.example/app"

    async def test_without_https_says_so_instead_of_a_dead_button(self, db, monkeypatch):
        """Telegram не откроет мини-приложение по http, и кнопка молча не сработает."""
        await register(db)
        monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000")

        invite = await commands.book(db, CHAT)

        assert invite.url is None
        assert "планшета" in invite.text

    async def test_unregistered_is_sent_to_start(self, db, monkeypatch):
        monkeypatch.setattr(settings, "public_base_url", "https://booking.example")

        invite = await commands.book(db, CHAT)

        assert invite.url is None
        assert "/start" in invite.text


class TestQueueCommands:
    async def test_join_and_leave(self, db, printers, make_user, outbox):
        await register(db)
        for printer in printers:
            await machines_svc.occupy(db, await make_user(), printer.id, 60)
        await db.commit()

        joined = await commands.queue_join(db, CHAT)
        assert "номер 1" in joined

        again = await commands.queue_join(db, CHAT)
        assert "уже в очереди" in again

        left = await commands.queue_leave(db, CHAT)
        assert left == texts.queue_left()

    async def test_leave_when_not_in_queue(self, db):
        await register(db)

        assert "нет в очереди" in (await commands.queue_leave(db, CHAT)).lower()

    async def test_user_with_active_print_cannot_queue(self, db, printers):
        await register(db)
        user = await user_of(db, CHAT)
        await machines_svc.occupy(db, user, printers[0].id, 60)
        await db.commit()

        answer = await commands.queue_join(db, CHAT)

        assert "уже занят" in answer

    async def test_joining_when_printer_is_free_sends_offer(self, db, printers, outbox):
        await register(db)

        await commands.queue_join(db, CHAT)

        assert len(outbox) == 1
        assert outbox[0][0] == CHAT
        assert "свободен" in outbox[0][1]


class TestFree:
    async def test_free_releases_my_printer(self, db, printers, outbox):
        machine_id = printers[0].id
        await register(db)
        user = await user_of(db, CHAT)
        await machines_svc.occupy(db, user, machine_id, 60)
        await db.commit()

        answer = await commands.free(db, CHAT)

        assert "освобождён" in answer
        db.expire_all()
        assert (await db.get(Machine, machine_id)).status == MachineStatus.FREE

    async def test_free_without_a_printer(self, db):
        await register(db)

        assert "не числится" in await commands.free(db, CHAT)

    async def test_free_notifies_only_the_first_in_queue(self, db, printers, make_user, outbox):
        """Правило 4 в уведомлениях: рассылки всем быть не должно."""
        await register(db)
        owner = await user_of(db, CHAT)
        second_owner = await make_user()
        first = await make_user()
        second = await make_user()
        await machines_svc.occupy(db, owner, printers[0].id, 60)
        await machines_svc.occupy(db, second_owner, printers[1].id, 60)
        await queue_svc.join(db, first.id, MachineKind.PRINTER)
        await queue_svc.join(db, second.id, MachineKind.PRINTER)
        await db.commit()
        outbox.clear()

        await commands.free(db, CHAT)

        assert len(outbox) == 1
        assert outbox[0][0] == first.tg_chat_id


class TestDelivery:
    async def test_blocked_bot_does_not_break_the_action(self, db, printers, make_user):
        """Сценарий приёмки 10: заблокировал бота — занятие всё равно работает."""

        async def failing(chat_id: int, text: str) -> None:
            raise RuntimeError("bot was blocked by the user")

        notify.set_sender(failing)
        try:
            await register(db)
            answer = await commands.queue_join(db, CHAT)
        finally:
            notify.set_sender(None)

        assert "номер 1" in answer

    async def test_send_is_a_noop_without_bot(self, db):
        notify.set_sender(None)

        assert await notify.send(1, "текст") is False

    async def test_send_to_unknown_user_is_safe(self, db, outbox):
        assert await notify.send_to_user(db, 999999, "текст") is False
        assert outbox == []


class TestTexts:
    @pytest.mark.parametrize(
        ("minutes", "expected"),
        [(30, "30 мин"), (60, "1 ч"), (252, "4 ч 12 мин"), (0, "0 мин")],
    )
    def test_humanize(self, minutes, expected):
        assert texts.humanize(minutes) == expected


class TestKindsInBot:
    """Очередей несколько, и команда обязана быть однозначной."""

    async def test_status_is_split_into_sections(self, db, printers, engravers, make_user):
        user = await make_user(name="Иван П.")
        await machines_svc.occupy(db, user, engravers[0].id, 60)

        answer = await commands.status(db)

        assert "Принтеры" in answer and "Гравировщики" in answer
        # гравировщик не печатает — слово статуса зависит от типа
        assert "гравирует" in answer
        assert "печатает" not in answer

    async def test_queue_without_kind_works_while_the_park_is_uniform(
        self, db, printers, make_user
    ):
        await register(db)
        user = await user_of(db, CHAT)
        for printer in printers:
            await machines_svc.occupy(db, await make_user(), printer.id, 60)

        answer = await commands.queue_join(db, user.tg_chat_id)

        assert "в очереди на принтер" in answer
        assert await queue_svc.position_of(db, user.id) == 1

    async def test_queue_without_kind_asks_which_one(self, db, printers, engravers, make_user):
        """Угаданное место в чужой очереди человек заметит через часы молчания."""
        await register(db)
        user = await user_of(db, CHAT)

        answer = await commands.queue_join(db, user.tg_chat_id)

        assert "/queue_printer" in answer and "/queue_engraver" in answer
        assert await queue_svc.position_of(db, user.id) is None

    async def test_queue_with_kind_joins_that_line(self, db, printers, engravers, make_user):
        await register(db)
        user = await user_of(db, CHAT)
        await machines_svc.occupy(db, await make_user(), engravers[0].id, 60)

        answer = await commands.queue_join(db, user.tg_chat_id, MachineKind.ENGRAVER)

        assert "в очереди на гравировщик" in answer

    async def test_queue_on_an_empty_park_says_so(self, db):
        await register(db)
        user = await user_of(db, CHAT)

        answer = await commands.queue_join(db, user.tg_chat_id)

        assert "нет ни одной машины" in answer

    async def test_my_state_names_the_line(self, db, printers, engravers, make_user):
        await register(db)
        user = await user_of(db, CHAT)
        await machines_svc.occupy(db, await make_user(), engravers[0].id, 60)
        await queue_svc.join(db, user.id, MachineKind.ENGRAVER)

        assert "в очереди на гравировщик" in await commands.my(db, user.tg_chat_id)


async def register(db, chat_id: int = CHAT, login: str = "i_petrov") -> str:
    """Оба шага регистрации: /start, потом логин ответным сообщением."""
    await commands.start(db, chat_id)
    return await commands.register(db, chat_id, login)


async def user_of(db, chat_id: int) -> User | None:
    return await db.scalar(select(User).where(User.tg_chat_id == chat_id))


def _pin_from(answer: str) -> str:
    """Достать PIN из сообщения бота: единственные четыре цифры в жирном."""
    bold = re.findall(r"<b>(\d{4})</b>", answer)
    assert len(bold) == 1, f"в сообщении не один PIN: {answer}"
    return bold[0]
