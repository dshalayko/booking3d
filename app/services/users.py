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
from app.services.errors import LoginInvalid, LoginTaken

# Корпоративный логин: `d_shalayko`. Две буквы до подчёркивания допускаем —
# у людей с двойным именем логин выглядит как `ab_surname`, и упереться в
# отказ им было бы некуда: другого способа зарегистрироваться нет.
# Длина укладывается в users.name (64).
LOGIN_RE = re.compile(r"[a-z]{1,2}_[a-z0-9-]{2,60}")


def normalize_login(value: str) -> str | None:
    """Корпоративный логин из того, что человек написал. None — это не логин."""
    login = (value or "").strip().lstrip("@").lower()
    return login if LOGIN_RE.fullmatch(login) else None


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
