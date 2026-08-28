"""Обращения из Mini App: принять форму и отдать список администратору."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.models import FeedbackRequest, User
from app.services.errors import FeedbackInvalid

USERNAME_MAX = 64
MESSAGE_MAX = 4000


async def create(
    db: AsyncSession, person: User, username: str, message: str
) -> FeedbackRequest:
    """Сохранить непустое обращение от подтверждённого пользователя Mini App."""
    clean_username = username.strip()
    clean_message = message.strip()
    if not clean_username or len(clean_username) > USERNAME_MAX:
        raise FeedbackInvalid(t.ERR_FEEDBACK_USERNAME)
    if not clean_message or len(clean_message) > MESSAGE_MAX:
        raise FeedbackInvalid(t.ERR_FEEDBACK_MESSAGE)

    item = FeedbackRequest(
        user_id=person.id,
        username=clean_username,
        message=clean_message,
    )
    db.add(item)
    await db.flush()
    return item


async def list_requests(db: AsyncSession) -> list[FeedbackRequest]:
    """Новые обращения сверху; id разбивает одинаковые отметки времени."""
    query = select(FeedbackRequest).order_by(
        FeedbackRequest.created_at.desc(), FeedbackRequest.id.desc()
    )
    return list((await db.scalars(query)).all())
