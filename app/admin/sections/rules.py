"""Правила бронирования, которые оператор может переключить без деплоя."""

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.services import booking_policy

router = core.section_router()

SECTION = core.Section(
    slug="rules",
    title=t.UI["admin_tab_rules"],
    icon="rules",
    router=router,
    group=core.GROUP_SETUP,
)


@router.get("/rules", response_class=HTMLResponse)
async def page(request: Request, db: Db, flash: str = "") -> Response:
    return core.render(
        request,
        SECTION,
        "admin/rules.html",
        {"multi_machine_enabled": await booking_policy.enabled(db)},
        flash,
    )


@router.post("/rules")
async def save(db: Db, multi_machine_enabled: str = Form("")) -> Response:
    await booking_policy.save(db, multi_machine_enabled == "on")
    await db.commit()
    return core.redirect("rules_saved", SECTION.path)
