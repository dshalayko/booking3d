"""Отправка уведомлений.

Домен ничего не знает про Telegram: сервисы возвращают результаты, а этот
модуль превращает их в сообщения. Сама отправка спрятана за
`Sender`, чтобы в тестах подменяться списком, а в бою — вызовом aiogram.

Ошибка отправки никогда не роняет запрос: человек заблокировал бота — это его
право, а занятие машины должно сработать (сценарий приёмки 10).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from html import escape

from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User

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


async def send_broadcast(chat_ids: list[int], text: str) -> tuple[int, int]:
    """Обычный текст, пауза между адресатами и одна повторная попытка при flood limit."""
    sender = _sender
    if sender is None:
        return 0, len(chat_ids)
    sent = 0
    payload = escape(text)
    for index, chat_id in enumerate(chat_ids):
        if index:
            await asyncio.sleep(0.05)
        try:
            try:
                await sender(chat_id, payload)
            except TelegramRetryAfter as error:
                await asyncio.sleep(error.retry_after)
                await sender(chat_id, payload)
            sent += 1
        except Exception:
            logger.warning("не удалось отправить рассылку в %s", chat_id, exc_info=True)
    return sent, len(chat_ids) - sent
