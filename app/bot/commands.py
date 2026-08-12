"""Логика команд бота.

Отделена от aiogram намеренно: команда — это чистая функция
`(сессия БД, кто написал) -> текст ответа`, поэтому её можно проверить тестом
без Telegram, вебхуков и моков. В `bot.py` остаётся только проводка.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notify, texts
from app.config import settings
from app.enums import ACTIVE_QUEUE_STATUSES, ACTIVE_SESSION_STATUSES
from app.models import Machine, MachineSession, QueueEntry, User
from app.services import auth
from app.services import board as board_svc
from app.services import machines as machines_svc
from app.services import queue as queue_svc
from app.services import reservations as reservations_svc
from app.services.errors import AlreadyInQueue, DomainError, NotInQueue
from app.services.users import normalize_login


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
    user = await _user(db, chat_id)
    if user is None:
        return texts.not_registered()

    session = await db.scalar(
        select(MachineSession).where(
            MachineSession.user_id == user.id,
            MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )
    machine_name = None
    if session is not None:
        machine = await db.get(Machine, session.machine_id)
        machine_name = machine.name if machine else None

    entry = await db.scalar(
        select(QueueEntry).where(
            QueueEntry.user_id == user.id,
            QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES),
        )
    )
    offered_machine = None
    if entry is not None and entry.offered_machine_id is not None:
        machine = await db.get(Machine, entry.offered_machine_id)
        offered_machine = machine.name if machine else None

    booking = await reservations_svc.active_of_user(db, user.id)
    booked_machine = None
    if booking is not None:
        machine = await db.get(Machine, booking.machine_id)
        booked_machine = machine.name if machine else None

    return texts.my_state(
        machine_name=machine_name,
        eta_at=session.eta_at if session else None,
        now=datetime.now(UTC),
        position=await queue_svc.position_of(db, user.id),
        queue_kind=entry.kind if entry else None,
        offered_machine=offered_machine,
        offer_until=entry.offer_expires_at if entry else None,
        booking_machine=booked_machine,
        booking_starts_at=booking.starts_at if booking else None,
        booking_ends_at=booking.ends_at if booking else None,
    )


async def queue_join(db: AsyncSession, chat_id: int, kind: str | None = None) -> str:
    """Встать в очередь на тип оборудования.

    Без типа команда однозначна только пока парк однороден: если в мастерской
    стоят и принтеры, и гравировщики, `/queue` не знает, чего человек ждёт, и
    отвечает списком команд по типам. Угадывать нельзя — угаданное место в
    чужой очереди человек заметит только через несколько часов молчания.
    """
    user = await _user(db, chat_id)
    if user is None:
        return texts.not_registered()

    if kind is None:
        kinds = await _kinds_in_park(db)
        if not kinds:
            return texts.park_empty()
        if len(kinds) > 1:
            return texts.queue_pick_kind(kinds)
        kind = kinds[0]

    try:
        result = await queue_svc.join(db, user.id, kind)
    except AlreadyInQueue:
        position = await queue_svc.position_of(db, user.id) or 1
        return texts.queue_already(position)
    except DomainError as error:
        return str(error)

    await db.commit()
    # Свободная машина могла найтись прямо сейчас — тогда предложение уже создано.
    await notify.announce_offers(db, result.offers)
    return texts.queue_joined(result.position, result.kind)


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
        select(MachineSession).where(
            MachineSession.user_id == user.id,
            MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )
    if session is None:
        return texts.nothing_to_free()

    try:
        result = await machines_svc.release(db, user, session.machine_id)
    except DomainError as error:
        return str(error)

    await db.commit()
    await notify.announce_offers(db, result.offers)
    return texts.released(result.machine_name)


async def _kinds_in_park(db: AsyncSession) -> list[str]:
    """Типы, машины которых реально стоят в мастерской.

    Спрашиваем базу, а не `MachineKind`: предлагать очередь на гравировщик там,
    где его нет, — значит поставить человека ждать машину, которая не появится.
    """
    park = await machines_svc.list_machines(db)
    kinds: list[str] = []
    for machine in park:
        if machine.kind not in kinds:
            kinds.append(machine.kind)
    return kinds


async def _user(db: AsyncSession, chat_id: int) -> User | None:
    return await db.scalar(select(User).where(User.tg_chat_id == chat_id))
