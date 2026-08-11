"""Логика команд бота.

Отделена от aiogram намеренно: команда — это чистая функция
`(сессия БД, кто написал) -> текст ответа`, поэтому её можно проверить тестом
без Telegram, вебхуков и моков. В `bot.py` остаётся только проводка.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notify, texts
from app.enums import ACTIVE_QUEUE_STATUSES, ACTIVE_SESSION_STATUSES
from app.models import Printer, PrintSession, QueueEntry, User
from app.services import auth
from app.services import board as board_svc
from app.services import printers as printers_svc
from app.services import queue as queue_svc
from app.services.errors import AlreadyInQueue, DomainError, NotInQueue
from app.services.users import normalize_login


async def start(db: AsyncSession, chat_id: int) -> str:
    """Первый шаг регистрации: спрашиваем логин.

    Имя из Telegram не берём — оно произвольное и меняется, а на доске и в
    журнале нужен тот же логин, что и в почте, иначе непонятно, кто занял
    принтер. PIN выдаётся только на втором шаге, вместе с логином.
    """
    user = await _user(db, chat_id)
    if user is not None:
        return texts.already_registered(user.name)
    return texts.ask_login()


async def register(db: AsyncSession, chat_id: int, value: str) -> str:
    """Второй шаг: логин пришёл обычным сообщением, выдаём PIN."""
    user = await _user(db, chat_id)
    if user is not None:
        return texts.already_registered(user.name)

    login = normalize_login(value)
    if login is None:
        return texts.bad_login()

    taken = await db.scalar(select(User.id).where(User.name == login))
    if taken is not None:
        return texts.login_taken(login)

    pin = await auth.pick_free_pin(db)
    db.add(User(tg_chat_id=chat_id, name=login, pin_digest=auth.pin_digest(pin)))
    await db.commit()
    return texts.welcome(login, pin)


async def text_message(db: AsyncSession, chat_id: int, value: str) -> str:
    """Обычный текст без команды.

    Незарегистрированному отвечает шаг регистрации: состояние диалога хранить
    не нужно — «нет пользователя» и есть состояние «ждём логин».
    """
    if await _user(db, chat_id) is not None:
        return texts.HELP
    if (value or "").strip().startswith("/"):
        return texts.not_registered()
    return await register(db, chat_id, value)


async def new_pin(db: AsyncSession, chat_id: int) -> str:
    user = await _user(db, chat_id)
    if user is None:
        return texts.not_registered()

    pin = await auth.assign_pin(db, user)
    await db.commit()
    return texts.pin_changed(pin)


async def status(db: AsyncSession) -> str:
    return texts.status(await board_svc.build(db))


async def my(db: AsyncSession, chat_id: int) -> str:
    user = await _user(db, chat_id)
    if user is None:
        return texts.not_registered()

    session = await db.scalar(
        select(PrintSession).where(
            PrintSession.user_id == user.id,
            PrintSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )
    printer_name = None
    if session is not None:
        printer = await db.get(Printer, session.printer_id)
        printer_name = printer.name if printer else None

    entry = await db.scalar(
        select(QueueEntry).where(
            QueueEntry.user_id == user.id,
            QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES),
        )
    )
    offered_printer = None
    if entry is not None and entry.offered_printer_id is not None:
        printer = await db.get(Printer, entry.offered_printer_id)
        offered_printer = printer.name if printer else None

    return texts.my_state(
        printer_name=printer_name,
        eta_at=session.eta_at if session else None,
        now=datetime.now(UTC),
        position=await queue_svc.position_of(db, user.id),
        offered_printer=offered_printer,
        offer_until=entry.offer_expires_at if entry else None,
    )


async def queue_join(db: AsyncSession, chat_id: int) -> str:
    user = await _user(db, chat_id)
    if user is None:
        return texts.not_registered()

    try:
        result = await queue_svc.join(db, user.id)
    except AlreadyInQueue:
        position = await queue_svc.position_of(db, user.id) or 1
        return texts.queue_already(position)
    except DomainError as error:
        return str(error)

    await db.commit()
    # Свободный принтер мог найтись прямо сейчас — тогда предложение уже создано.
    await notify.announce_offers(db, result.offers)
    return texts.queue_joined(result.position)


async def queue_leave(db: AsyncSession, chat_id: int) -> str:
    user = await _user(db, chat_id)
    if user is None:
        return texts.not_registered()

    try:
        result = await queue_svc.leave(db, user.id)
    except NotInQueue as error:
        return str(error)

    await db.commit()
    await notify.announce_offers(db, result.offers)
    return texts.queue_left()


async def free(db: AsyncSession, chat_id: int) -> str:
    user = await _user(db, chat_id)
    if user is None:
        return texts.not_registered()

    session = await db.scalar(
        select(PrintSession).where(
            PrintSession.user_id == user.id,
            PrintSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )
    if session is None:
        return texts.nothing_to_free()

    try:
        result = await printers_svc.release(db, user, session.printer_id)
    except DomainError as error:
        return str(error)

    await db.commit()
    await notify.announce_offers(db, result.offers)
    return texts.released(result.printer_name)


async def _user(db: AsyncSession, chat_id: int) -> User | None:
    return await db.scalar(select(User).where(User.tg_chat_id == chat_id))
