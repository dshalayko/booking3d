"""«Журнал» — что происходило с парком.

Только чтение: кнопок здесь нет и не будет — журнал собирается из таблиц
(`services/activity.py`), своей записи у него нет, и «удалить событие» означало
бы удалить сессию или бронь. Отдельная страница потому же, почему брони: список
длинный и растёт, а на «Сводке» он оттеснял бы вниз срочные кнопки.
"""

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.services import activity as activity_svc

router = core.section_router()

SECTION = core.Section(
    slug="log",
    title=t.UI["admin_nav_events"],
    heading=t.UI["admin_events"],
    icon="list",
    router=router,
    group=core.GROUP_NOW,
)


@router.get("/log", response_class=HTMLResponse)
async def page(request: Request, db: Db, flash: str = "") -> Response:
    return core.render(
        request, SECTION, "admin/log.html", {"events": await activity_svc.recent(db)}, flash
    )
