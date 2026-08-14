"""«Часы работы» — когда какое помещение открыто.

Карточка на каждое помещение: переговорная закрывается в шесть, мастерская
работает до ночи. Свой раздел, а не поле среди срочных кнопок «Сводки»: правка
редкая, зато меняет и то, что видно на стене, и то, что вообще можно
забронировать, — по той же причине, по которой свой раздел есть у состава парка.
"""

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.services import rooms as rooms_svc
from app.services import workhours as workhours_svc

router = core.section_router()

SECTION = core.Section(
    slug="hours",
    title=t.UI["admin_tab_hours"],
    icon="clock",
    router=router,
    group=core.GROUP_SETUP,
)


@router.get("/hours", response_class=HTMLResponse)
async def page(request: Request, db: Db, flash: str = "") -> Response:
    rooms = await rooms_svc.list_rooms(db)
    hours = await workhours_svc.by_room(db, [room.id for room in rooms])
    return core.render(
        request,
        SECTION,
        "admin/hours.html",
        {"rows": [{"room": room, "hours": hours[room.id]} for room in rooms]},
        flash,
    )


@router.post("/hours/{room_id}")
async def save(
    db: Db, room_id: int, opens_at: str = Form(""), closes_at: str = Form("")
) -> Response:
    """Записать новые часы помещения.

    Уже сделанные брони не трогаются: снять чужое окно, потому что помещение
    стало закрываться на час раньше, — это решение человека, а не побочный
    эффект правки формы. Такие брони видны в разделе «Брони», и оттуда же
    снимаются.
    """
    await rooms_svc.get(db, room_id)
    await workhours_svc.save(db, room_id, opens_at, closes_at)
    await db.commit()
    return core.redirect("hours_saved", SECTION.path)
