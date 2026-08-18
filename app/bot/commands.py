"""Логика команд бота.

Отделена от aiogram намеренно: команда — это чистая функция
`(сессия БД, кто написал) -> текст ответа`, поэтому её можно проверить тестом
без Telegram, вебхуков и моков. В `bot.py` остаётся только проводка.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.config import settings
from app.enums import ACTIVE_SESSION_STATUSES
from app.models import Machine, MachineSession, Room, User
from app.services import auth
from app.services import board as board_svc
from app.services import machines as machines_svc
from app.services import reservations as reservations_svc
from app.services.errors import DomainError
from app.services.users import normalize_login


@dataclass(frozen=True)
class Reply:
    """Текст ответа и признак «в нём выдан PIN».

    Признак нужен проводке: сообщение с PIN-ом она закрепляет наверху чата,
    чтобы четыре цифры не утонули в переписке. Искать PIN в готовом тексте
    регулярным выражением значило бы держать формат фразы в двух местах —
    поэтому о PIN-е говорит тот, кто его выдал.
    """

    text: str
    pin: bool = False


async def start(db: AsyncSession, chat_id: int) -> str:
    """Первый шаг регистрации: спрашиваем логин.

    Имя из Telegram не берём — оно произвольное и меняется, а на доске и в
    журнале нужен тот же логин, что и в почте, иначе непонятно, кто занял
    машину. PIN выдаётся только на втором шаге, вместе с логином.
    """
    user = await _user(db, chat_id)
    if user is not None:
        return texts.already_registered(user.name)
    return texts.ask_login()


async def register(db: AsyncSession, chat_id: int, value: str) -> Reply:
    """Второй шаг: логин пришёл обычным сообщением, выдаём PIN."""
    user = await _user(db, chat_id)
    if user is not None:
        return Reply(texts.already_registered(user.name))

    login = normalize_login(value)
    if login is None:
        return Reply(texts.bad_login())

    taken = await db.scalar(select(User.id).where(User.name == login))
    if taken is not None:
        return Reply(texts.login_taken(login))

    pin = await auth.pick_free_pin(db)
    db.add(User(tg_chat_id=chat_id, name=login, pin_digest=auth.pin_digest(pin)))
    await db.commit()
    return Reply(texts.welcome(login, pin), pin=True)


async def text_message(db: AsyncSession, chat_id: int, value: str) -> Reply:
    """Обычный текст без команды.

    Незарегистрированному отвечает шаг регистрации: состояние диалога хранить
    не нужно — «нет пользователя» и есть состояние «ждём логин».
    """
    if await _user(db, chat_id) is not None:
        return Reply(texts.HELP)
    if (value or "").strip().startswith("/"):
        return Reply(texts.not_registered())
    return await register(db, chat_id, value)


async def new_pin(db: AsyncSession, chat_id: int) -> Reply:
    user = await _user(db, chat_id)
    if user is None:
        return Reply(texts.not_registered())

    pin = await auth.assign_pin(db, user)
    await db.commit()
    return Reply(texts.pin_changed(pin), pin=True)


def app_url() -> str | None:
    """Адрес Mini App или None, если Telegram его не откроет.

    Telegram грузит мини-приложения только по https и только с настоящим
    сертификатом: на локальном `http://localhost:8000` кнопка молча не сработает,
    и лучше сказать об этом словами, чем отправить человека в никуда.
    """
    base = settings.public_base_url.rstrip("/")
    return f"{base}/app" if base.startswith("https://") else None


@dataclass(frozen=True)
class Invite:
    """Ответ на /book: текст и адрес, на который вешается кнопка Mini App.

    Кнопку собирает bot.py — здесь про aiogram по-прежнему ничего не знают.
    """

    text: str
    url: str | None = None


async def book(db: AsyncSession, chat_id: int) -> Invite:
    """Пригласить в расписание: там и брони на будущее, и всё остальное."""
    user = await _user(db, chat_id)
    if user is None:
        return Invite(texts.not_registered())

    url = app_url()
    if url is None:
        return Invite(texts.book_no_app())
    return Invite(texts.book_invite(), url)


async def status(db: AsyncSession) -> str:
    return texts.status(await board_svc.build(db))


async def my(db: AsyncSession, chat_id: int) -> str:
    """Всё, что за человеком числится, — по всем помещениям сразу.

    Списки сохраняют корректное отображение исторических данных, даже если в
    старой базе до глобального лимита у человека оказалось несколько записей.
    """
    user = await _user(db, chat_id)
    if user is None:
        return texts.not_registered()

    works = [
        texts.MyWork(machine=machine.name, room=room.name, eta_at=session.eta_at)
        for session, machine, room in (
            await db.execute(
                select(MachineSession, Machine, Room)
                .join(Machine, Machine.id == MachineSession.machine_id)
                .join(Room, Room.id == MachineSession.room_id)
                .where(
                    MachineSession.user_id == user.id,
                    MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
                )
                .order_by(MachineSession.started_at)
            )
        ).all()
    ]

    bookings = [
        texts.MyBooking(
            machine=machine.name,
            room=room.name,
            starts_at=booking.starts_at,
            ends_at=booking.ends_at,
        )
        for booking, machine, room in await reservations_svc.of_user(db, user.id)
    ]

    return texts.my_state(now=datetime.now(UTC), works=works, bookings=bookings)


async def free(db: AsyncSession, chat_id: int) -> str:
    """Освободить своё. Что именно — вопрос, если занято сразу в двух комнатах."""
    user = await _user(db, chat_id)
    if user is None:
        return texts.not_registered()

    rows = (
        await db.execute(
            select(MachineSession, Machine, Room)
            .join(Machine, Machine.id == MachineSession.machine_id)
            .join(Room, Room.id == MachineSession.room_id)
            .where(
                MachineSession.user_id == user.id,
                MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
            )
            .order_by(MachineSession.started_at)
        )
    ).all()
    if not rows:
        return texts.nothing_to_free()
    if len(rows) > 1:
        return texts.free_pick([(room.name, machine.name) for _, machine, room in rows])

    session = rows[0][0]
    try:
        result = await machines_svc.release(db, user, session.machine_id)
    except DomainError as error:
        return str(error)

    await db.commit()
    return texts.released(result.machine_name)


async def _user(db: AsyncSession, chat_id: int) -> User | None:
    return await db.scalar(select(User).where(User.tg_chat_id == chat_id))
