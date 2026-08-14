"""Логин человека: формат и переименование.

`users.name` — это корпоративный логин, тот же, что в почте: под ним человека
видно на доске, в очереди и в журнале. Спрашивает его бот при регистрации, но
исправить там уже нельзя — второй раз зарегистрироваться не даёт `tg_chat_id`,
а команды «сменить логин» у бота нет намеренно: тогда занявший принтер мог бы
переименоваться в кого-то другого. Поэтому опечатку правит админ, а правило
формата лежит здесь — одно для регистрации и для правки.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.models import User
from app.services.errors import (
    AuthFailed,
    ChatIdInvalid,
    ChatIdTaken,
    LoginInvalid,
    LoginTaken,
    NotAdmin,
    PinTaken,
)
from app.services.security import is_valid_pin_format, pin_digest

# Корпоративный логин: `d_shalayko`. Две буквы до подчёркивания допускаем —
# у людей с двойным именем логин выглядит как `ab_surname`, и упереться в
# отказ им было бы некуда: другого способа зарегистрироваться нет.
# Длина укладывается в users.name (64).
LOGIN_RE = re.compile(r"[a-z]{1,2}_[a-z0-9-]{2,60}")


def normalize_login(value: str) -> str | None:
    """Корпоративный логин из того, что человек написал. None — это не логин."""
    login = (value or "").strip().lstrip("@").lower()
    return login if LOGIN_RE.fullmatch(login) else None


async def create(
    db: AsyncSession, admin: User, login: str, tg_chat_id: int, pin: str
) -> User:
    """Завести человека из админки.

    Обычный путь — регистрация у бота: там `tg_chat_id` приходит от Telegram, а
    PIN выдаётся случайный и уходит человеку в личку. Здесь оба задаются руками,
    и это нужно ровно для двух случаев: тестовые учётки (у которых нет и не
    будет настоящего Telegram) и человек, который до бота дойти не может.

    PIN просим ввести, а не генерируем: сгенерированный пришлось бы показать на
    экране, а он оттуда попадает и в историю браузера, и в плечо соседа. Тот,
    кто заводит учётку, всё равно должен продиктовать PIN — пусть диктует то,
    что сам и набрал.
    """
    if not admin.is_admin:
        raise NotAdmin(t.ERR_ADMIN_ONLY)

    name = normalize_login(login)
    if name is None:
        raise LoginInvalid(t.ERR_LOGIN_FORMAT)
    if await db.scalar(select(User.id).where(User.name == name)) is not None:
        raise LoginTaken(t.ERR_LOGIN_TAKEN.format(login=name))

    if tg_chat_id <= 0:
        raise ChatIdInvalid(t.ERR_CHAT_ID_FORMAT)
    if await db.scalar(select(User.id).where(User.tg_chat_id == tg_chat_id)) is not None:
        raise ChatIdTaken(t.ERR_CHAT_ID_TAKEN.format(chat_id=tg_chat_id))

    if not is_valid_pin_format(pin):
        raise AuthFailed(t.ERR_PIN_FORMAT)
    digest = pin_digest(pin)
    if await db.scalar(select(User.id).where(User.pin_digest == digest)) is not None:
        raise PinTaken(t.ERR_PIN_TAKEN)

    person = User(tg_chat_id=tg_chat_id, name=name, pin_digest=digest)
    db.add(person)
    await db.flush()
    return person


async def list_people(db: AsyncSession) -> list[User]:
    """Все зарегистрированные, по логину. Список нужен и разделу «Люди», и
    цифрам на «Сводке» — выборка одна, и жить ей лучше здесь."""
    return list((await db.scalars(select(User).order_by(User.name))).all())


async def rename(db: AsyncSession, user: User, value: str) -> str:
    """Сменить логин. Возвращает прежний — его показывает плашка и уведомление.

    Проверка занятости прикладная, как и при регистрации: уникального индекса
    на `users.name` нет, но переименовывает один оператор из админки, и гонка
    здесь не бывает.
    """
    login = normalize_login(value)
    if login is None:
        raise LoginInvalid(t.ERR_LOGIN_FORMAT)

    taken = await db.scalar(select(User.id).where(User.name == login, User.id != user.id))
    if taken is not None:
        raise LoginTaken(t.ERR_LOGIN_TAKEN.format(login=login))

    previous = user.name
    user.name = login
    await db.flush()
    return previous
