"""Отправка уведомлений.

Домен ничего не знает про Telegram: сервисы возвращают `Offer` и прочие
результаты, а этот модуль превращает их в сообщения. Сама отправка спрятана за
`Sender`, чтобы в тестах подменяться списком, а в бою — вызовом aiogram.

Ошибка отправки никогда не роняет запрос: человек заблокировал бота — это его
право, а занятие принтера должно сработать (сценарий приёмки 10).
"""

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.models import User
from app.services.queue import Offer

logger = logging.getLogger(__name__)

Sender = Callable[[int, str], Awaitable[None]]

_sender: Sender | None = None


def set_sender(sender: Sender | None) -> None:
    global _sender
    _sender = sender


def is_configured() -> bool:
    return _sender is not None


async def send(chat_id: int, text: str) -> bool:
    """Отправить сообщение. Возвращает, дошло ли — но не бросает исключений."""
    if _sender is None:
        logger.debug("бот не настроен, сообщение в %s не отправлено", chat_id)
        return False
    try:
        await _sender(chat_id, text)
        return True
    except Exception:  # заблокировал бота, удалил чат, Telegram лежит
        logger.warning("не удалось отправить сообщение в %s", chat_id, exc_info=True)
        return False


async def send_to_user(db: AsyncSession, user_id: int, text: str) -> bool:
    chat_id = await db.scalar(select(User.tg_chat_id).where(User.id == user_id))
    if chat_id is None:
        return False
    return await send(chat_id, text)


async def announce_offers(db: AsyncSession, offers: list[Offer]) -> None:
    """Правило 4: сообщение уходит только тому, кому сделано предложение."""
    for item in offers:
        await send_to_user(db, item.user_id, texts.offer(item.printer_name, item.expires_at))
