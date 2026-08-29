"""«Оборудование» — состав парка: завести машину, переименовать, удалить.

Действия редкие, зато меняют то, что видно на стене, поэтому им своя страница,
а не ещё один блок среди срочных кнопок. Менять состояние машины («в
обслуживание», «вернуть в строй») — это «Сводка»: см. sections/summary.py.
"""

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.services import machines as machines_svc
from app.services import purge
from app.services import rooms as rooms_svc

router = core.section_router()

SECTION = core.Section(
    slug="machines",
    title=t.UI["admin_tab_machines"],
    icon="printer",
    router=router,
    group=core.GROUP_SETUP,
)


@router.get("/machines", response_class=HTMLResponse)
async def page(request: Request, db: Db, flash: str = "") -> Response:
    """Что где стоит и что с этим можно сделать.

    Сгруппировано по помещениям, а не по типам: заводят машину «в переговорную»
    или «в дальнюю мастерскую», и искать её в списке тоже по комнате.

    Рядом с каждой машиной показано, сколько за ней записей в журнале: удалить
    можно только машину без истории, и лучше объяснить это заранее, чем отказом
    после нажатия.
    """
    rooms = await rooms_svc.list_rooms(db)
    rows = [
        {"machine": machine, "usage": await machines_svc.usage(db, machine.id)}
        for machine in await machines_svc.list_machines(db)
    ]
    return core.render(
        request,
        SECTION,
        "admin/machines.html",
        {
            "rooms": rooms,
            "groups": [
                {
                    "room": room,
                    "rows": [row for row in rows if row["machine"].room_id == room.id],
                }
                for room in rooms
            ],
        },
        flash,
    )


@router.post("/machines")
async def add(
    request: Request,
    db: Db,
    name: str = Form(""),
    kind: str = Form(""),
    room_id: int = Form(0),
) -> Response:
    admin = await core.acting_admin(db, request)
    await machines_svc.create(db, admin, room_id, name, kind)
    await db.commit()
    return core.redirect("machine_added", SECTION.path)


@router.post("/machines/{machine_id}/name")
async def rename(
    request: Request, db: Db, machine_id: int, name: str = Form("")
) -> Response:
    admin = await core.acting_admin(db, request)
    await machines_svc.rename(db, admin, machine_id, name)
    await db.commit()
    return core.redirect("machine_renamed", SECTION.path)


@router.get("/machines/{machine_id}/delete", response_class=HTMLResponse)
async def confirm_delete(request: Request, db: Db, machine_id: int) -> Response:
    """Что уедет вместе с машиной. Кнопка в списке ведёт сюда, а не сразу в POST."""
    machine = await machines_svc.get(db, machine_id)
    return core.confirm_delete(
        request,
        SECTION,
        machine.name,
        await purge.machine_fallout(db, machine_id),
        f"/admin/machines/{machine_id}/delete",
        t.UI["admin_delete_instead_machine"],
    )


@router.post("/machines/{machine_id}/delete")
async def delete(
    request: Request, db: Db, machine_id: int, confirm: str = Form("")
) -> Response:
    """Удалить машину.

    Без подтверждения работает старое правило: удаляется только машина без
    единой записи в журнале, иначе отказ с объяснением. Подтверждение приходит
    с экрана `confirm_delete` и означает «да, вместе с историей» — оно и есть
    единственный способ снести машину, за которой что-то записано.
    """
    admin = await core.acting_admin(db, request)
    if confirm:
        await purge.purge_machine(db, admin, machine_id)
    else:
        await machines_svc.remove(db, admin, machine_id)
    await db.commit()
    return core.redirect("machine_purged" if confirm else "machine_removed", SECTION.path)
