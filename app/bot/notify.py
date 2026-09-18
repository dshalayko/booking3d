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

Sender = Callable[[int, str], Awaitable[object | None]]

_sender: Sender | None = None


def set_sender(sender: Sender | None) -> None:
    global _sender
    _sender = sender


def is_configured() -> bool:
    return _sender is not None


async def send(chat_id: int, text: str) -> bool:
    """Отправить сообщение. Возвращает, дошло ли — но не бросает исключений."""
    delivered, _ = await _deliver(chat_id, text)
    return delivered


async def _deliver(chat_id: int, text: str) -> tuple[bool, object | None]:
    """Отправить и сохранить объект Telegram-сообщения, если проводка его вернула."""
    if _sender is None:
        logger.debug("бот не настроен, сообщение в %s не отправлено", chat_id)
        return False, None
    try:
        return True, await _sender(chat_id, text)
    except Exception:  # заблокировал бота, удалил чат, Telegram лежит
        logger.warning("не удалось отправить сообщение в %s", chat_id, exc_info=True)
        return False, None


async def send_to_user(db: AsyncSession, user_id: int, text: str) -> bool:
    chat_id = await db.scalar(select(User.tg_chat_id).where(User.id == user_id))
    if chat_id is None:
        return False
    return await send(chat_id, text)


async def send_pin_to_user(
    db: AsyncSession, user_id: int, text: str, pin_text: str
) -> bool:
    """Отправить пояснение и следом отдельную закреплённую карточку с PIN."""
    chat_id = await db.scalar(select(User.tg_chat_id).where(User.id == user_id))
    if chat_id is None:
        return False

    intro_delivered, _ = await _deliver(chat_id, text)
    pin_delivered, sent = await _deliver(chat_id, pin_text)
    if not pin_delivered:
        return False

    # Тестовые и альтернативные отправители могут не возвращать Telegram Message.
    # В бою bot.send_message возвращает его, и карточку можно сразу закрепить.
    if sent is not None and hasattr(sent, "pin"):
        try:
            await sent.chat.unpin_all_messages()
            await sent.pin(disable_notification=True)
        except Exception:  # закрепление — удобство, доставка PIN важнее
            logger.warning("не удалось закрепить сообщение с PIN-ом", exc_info=True)
    return intro_delivered


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
