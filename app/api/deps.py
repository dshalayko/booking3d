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
from app.services import auth


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


Db = Annotated[AsyncSession, Depends(get_db)]


def require_kiosk_device(request: Request) -> None:
    """Правило 11: PIN вводится только на устройстве-киоске."""
    if not auth.is_kiosk_device(request.cookies.get(auth.DEVICE_COOKIE)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t.ERR_KIOSK_ONLY)


def require_admin(request: Request) -> None:
    if not auth.is_admin_session(request.cookies.get(auth.ADMIN_COOKIE)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t.ERR_ADMIN_LOGIN_REQUIRED)


def is_kiosk(request: Request) -> bool:
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
