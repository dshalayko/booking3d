"""Сообщения от администратора всем или выбранным пользователям бота."""

from typing import Annotated

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.bot import notify
from app.services import broadcasts, users

router = core.section_router()
SECTION = core.Section(
    slug="broadcasts", title=t.UI["admin_nav_broadcasts"], icon="message", router=router,
)


@router.get("/broadcasts", response_class=HTMLResponse)
async def page(
    request: Request, db: Db, user_id: int | None = None,
    sent: int | None = None, failed: int | None = None, audience: str = "selected",
) -> Response:
    return core.render(request, SECTION, "admin/broadcasts.html", {
        "users": await users.list_people(db),
        "selected": [user_id] if user_id is not None else [],
        "audience": "all" if audience == "all" else "selected", "message": "",
        "configured": notify.is_configured(),
        "result": t.UI["broadcast_result"].format(sent=sent, failed=failed)
        if sent is not None and failed is not None else None,
    })


@router.post("/broadcasts")
async def send(
    request: Request, db: Db, user_ids: Annotated[list[int] | None, Form()] = None,
    message: str = Form(""), audience: str = Form("selected"),
) -> Response:
    user_ids = user_ids or []
    try:
        if not notify.is_configured():
            raise ValueError(t.UI["broadcast_not_configured"])
        message, chat_ids = await broadcasts.prepare(db, message, audience, user_ids)
    except ValueError as error:
        response = core.render(request, SECTION, "admin/broadcasts.html", {
            "users": await users.list_people(db), "selected": user_ids,
            "audience": audience, "message": message, "error": str(error),
            "configured": notify.is_configured(),
        })
        response.status_code = 400
        return response
    # Не держим транзакцию открытой во время сетевых запросов.
    await db.rollback()
    sent, failed = await notify.send_broadcast(chat_ids, message)
    return RedirectResponse(
        f"{SECTION.path}?sent={sent}&failed={failed}", status_code=303,
    )
