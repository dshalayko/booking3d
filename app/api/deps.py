"""Зависимости FastAPI: сессия БД и проверки доступа.

«Текущего пользователя» у киоска нет: вошедшего между запросами не помним,
каждое действие подписывается PIN заново. См. services/auth.py.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.config import settings
from app.db import SessionLocal
from app.models import User
from app.services import auth


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


Db = Annotated[AsyncSession, Depends(get_db)]


def require_kiosk_device(request: Request) -> None:
    """Правило 11: PIN вводится только на устройстве-киоске."""
    if not is_kiosk(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t.ERR_KIOSK_ONLY)


async def require_admin(request: Request, db: Db) -> None:
    """Пустить с ADMIN_SECRET или из Mini App администратора.

    Telegram-сессия указывает на конкретного человека, поэтому роль проверяем
    в базе при каждом запросе. Снятие ``is_admin`` отзывает доступ сразу.
    """
    request.state.admin_from_app = False
    request.state.admin_user_id = None
    if auth.is_admin_session(request.cookies.get(auth.ADMIN_COOKIE)):
        return

    user_id = auth.app_session_user_id(request.cookies.get(auth.APP_COOKIE))
    person = await db.get(User, user_id) if user_id is not None else None
    if person is None or not person.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, t.ERR_ADMIN_LOGIN_REQUIRED)
    request.state.admin_from_app = True
    request.state.admin_user_id = person.id


def kiosk_room_id(request: Request) -> int | None:
    """Помещение этого планшета, если он к нему привязан.

    Здесь, а рядом с `is_kiosk`, потому что оба ответа читает один и тот же
    роутер: первый решает, можно ли вводить PIN, второй — что вообще показывать.
    """
    return auth.device_room_id(request.cookies.get(auth.DEVICE_COOKIE))


def is_kiosk(request: Request) -> bool:
    """Может ли этот запрос вводить PIN.

    Проверка здесь, а не в `auth.is_kiosk_device`: та отвечает на вопрос
    «подписана ли эта cookie нашим секретом», и подмешивать в неё режим доступа
    значило бы, что при `KIOSK_OPEN_ACCESS` подделанный токен считается
    настоящим.
    """
    if settings.kiosk_open_access:
        return True
    return auth.is_kiosk_device(request.cookies.get(auth.DEVICE_COOKIE))


def client_key(request: Request) -> str:
    """Ключ для ограничения попыток: устройство, иначе адрес."""
    device = request.cookies.get(auth.DEVICE_COOKIE)
    if device:
        return f"device:{device[:32]}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def cookie_params(max_age: int) -> dict:
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": settings.public_base_url.startswith("https"),
        "max_age": max_age,
        "path": "/",
    }
