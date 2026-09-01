"""Тестовые возможности, которые можно включать без нового деплоя."""

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.services import feature_flags

router = core.section_router()

SECTION = core.Section(
    slug="features",
    title=t.UI["admin_tab_features"],
    icon="rules",
    router=router,
    group=core.GROUP_SETUP,
)


@router.get("/features", response_class=HTMLResponse)
async def page(request: Request, db: Db, flash: str = "") -> Response:
    return core.render(
        request,
        SECTION,
        "admin/features.html",
        {"slicer_enabled": await feature_flags.slicer_enabled(db)},
        flash,
    )


@router.post("/features/slicer")
async def save_slicer(db: Db, slicer_enabled: str = Form("")) -> Response:
    await feature_flags.save_slicer(db, slicer_enabled == "on")
    await db.commit()
    return core.redirect("features_saved", SECTION.path)
