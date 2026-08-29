"""«Помещения» — комнаты: завести, переименовать, удалить.

Помещение — граница правил (свои лимиты и свои часы), поэтому
раздел первый из редких: без комнаты в системе нельзя ни завести машину, ни
задать часы.
"""

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.services import purge
from app.services import rooms as rooms_svc
from app.services import workhours as workhours_svc

router = core.section_router()

SECTION = core.Section(
    slug="rooms",
    title=t.UI["admin_tab_rooms"],
    icon="door",
    router=router,
    group=core.GROUP_SETUP,
)


@router.get("/rooms", response_class=HTMLResponse)
async def page(request: Request, db: Db, flash: str = "") -> Response:
    """Какие комнаты есть и что с ними можно сделать.

    Рядом с каждой — часы работы, адрес для планшета и то, что мешает удалить:
    удалить можно только пустую комнату, и лучше объяснить это заранее, чем
    отказом после нажатия.
    """
    rooms = await rooms_svc.list_rooms(db)
    hours = await workhours_svc.by_room(db, [room.id for room in rooms])
    return core.render(
        request,
        SECTION,
        "admin/rooms.html",
        {
            "rows": [
                {
                    "room": room,
                    "usage": await rooms_svc.usage(db, room.id),
                    "hours": hours[room.id],
                }
                for room in rooms
            ]
        },
        flash,
    )


@router.post("/rooms")
async def add(
    request: Request, db: Db, name: str = Form(""), kind: str = Form("")
) -> Response:
    admin = await core.acting_admin(db, request)
    await rooms_svc.create(db, admin, name, kind)
    await db.commit()
    return core.redirect("room_added", SECTION.path)


@router.post("/rooms/{room_id}/name")
async def rename(
    request: Request, db: Db, room_id: int, name: str = Form("")
) -> Response:
    admin = await core.acting_admin(db, request)
    await rooms_svc.rename(db, admin, room_id, name)
    await db.commit()
    return core.redirect("room_renamed", SECTION.path)


@router.get("/rooms/{room_id}/delete", response_class=HTMLResponse)
async def confirm_delete(request: Request, db: Db, room_id: int) -> Response:
    """Что уедет вместе с помещением — вместе с его машинами и их историей."""
    room = await rooms_svc.get(db, room_id)
    return core.confirm_delete(
        request,
        SECTION,
        room.name,
        await purge.room_fallout(db, room_id),
        f"/admin/rooms/{room_id}/delete",
    )


@router.post("/rooms/{room_id}/delete")
async def delete(
    request: Request, db: Db, room_id: int, confirm: str = Form("")
) -> Response:
    """Удалить помещение.

    Без подтверждения — старое правило: только пустую комнату. С подтверждением
    уезжает всё, что в ней стояло и что за ней записано.
    """
    admin = await core.acting_admin(db, request)
    if confirm:
        await purge.purge_room(db, admin, room_id)
    else:
        await rooms_svc.remove(db, admin, room_id)
    await db.commit()
    return core.redirect("room_purged" if confirm else "room_removed", SECTION.path)
