"""Проверка текста и получателей рассылки без отправки в Telegram."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.models import User


async def prepare(
    db: AsyncSession, message: str, audience: str, user_ids: list[int]
) -> tuple[str, list[int]]:
    message = message.strip()
    if not message or len(message.encode("utf-16-le")) // 2 > 4096:
        raise ValueError(t.UI["broadcast_invalid_text"])
    if audience not in {"all", "selected"}:
        raise ValueError(t.UI["broadcast_invalid_audience"])
    query = select(User.id, User.tg_chat_id).where(User.tg_chat_id > 0).order_by(User.id)
    if audience == "selected":
        if not user_ids:
            raise ValueError(t.UI["broadcast_choose_users"])
        query = query.where(User.id.in_(set(user_ids)))
    rows = (await db.execute(query)).all()
    if audience == "selected" and {row.id for row in rows} != set(user_ids):
        raise ValueError(t.UI["broadcast_missing_users"])
    if not rows:
        raise ValueError(t.UI["broadcast_no_users"])
    return message, [row.tg_chat_id for row in rows]
