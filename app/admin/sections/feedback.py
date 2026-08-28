"""Обращения, отправленные пользователями из Telegram Mini App."""

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.services import feedback as feedback_svc

router = core.section_router()

SECTION = core.Section(
    slug="feedback",
    title=t.UI["admin_feedback"],
    icon="message",
    router=router,
    group=core.GROUP_NOW,
)


@router.get("/feedback", response_class=HTMLResponse)
async def page(request: Request, db: Db) -> Response:
    return core.render(
        request,
        SECTION,
        "admin/feedback.html",
        {"requests": await feedback_svc.list_requests(db)},
    )
