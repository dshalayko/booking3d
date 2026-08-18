"""«Сводка» — состояние парка прямо сейчас и кнопки, которыми его чинят.

Сюда заходят, когда что-то пошло не так: работа зависла или машина сломалась.
Всё остальное — брони, люди, журнал — живёт в
своих разделах: там смотрят, а здесь действуют.

Действия над машиной («в обслуживание», «вернуть в строй», «снять работу»)
принадлежат этому разделу, а не разделу «Оборудование», хотя адреса у них
общие — `/admin/machines/{id}/...`. Граница проходит по смыслу: здесь меняют
состояние машины, там — состав парка. Адреса оставлены как есть: их знают
закладки и тесты, а красота URL этого не стоит.
"""

from fastapi import Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.bot import notify, texts
from app.enums import MachineStatus
from app.models import User
from app.services import board as board_svc
from app.services import machines as machines_svc
from app.services import reservations as reservations_svc
from app.services import users as users_svc

router = core.section_router()

SECTION = core.Section(
    slug="",
    title=t.UI["admin_tab_summary"],
    icon="grid",
    router=router,
    group=core.GROUP_NOW,
)


def counts(board: board_svc.Board, bookings: list, users: list[User]) -> dict[str, int]:
    """Цифры парка для карточек наверху.

    Считаются здесь, а не в шаблоне: `selectattr` по статусам в разметке
    читается хуже строчки на Python, а вопрос «что считается занятым» ещё и
    доменный — работа на машине и деталь, оставленная на столе, это одно
    состояние «машина не свободна», хотя статуса два.
    """
    machines = board.machines
    return {
        "machines": len(machines),
        "free": board.free_count,
        "busy": sum(
            1
            for machine in machines
            if machine.status in (MachineStatus.PRINTING, MachineStatus.DONE_WAIT)
        ),
        "broken": sum(1 for machine in machines if machine.status == MachineStatus.BROKEN),
        "bookings": len(bookings),
        "people": len(users),
    }


@router.get("", response_class=HTMLResponse)
async def page(request: Request, db: Db, flash: str = "") -> Response:
    board = await board_svc.build(db)
    return core.render(
        request,
        SECTION,
        "admin/summary.html",
        {
            "board": board,
            "counts": counts(
                board,
                await reservations_svc.booked_ahead(db),
                await users_svc.list_people(db),
            ),
        },
        flash,
    )


@router.post("/machines/{machine_id}/break")
async def break_machine(db: Db, machine_id: int, note: str = Form("")) -> Response:
    admin = await core.acting_admin(db)
    result = await machines_svc.set_broken(db, admin, machine_id, note=note.strip() or None)
    await db.commit()

    if result.owner_user_id is not None:
        await notify.send_to_user(
            db,
            result.owner_user_id,
            texts.work_cancelled_by_admin(result.machine_name, note.strip() or None),
        )
    return core.redirect("broken")


@router.post("/machines/{machine_id}/fix")
async def fix_machine(db: Db, machine_id: int) -> Response:
    admin = await core.acting_admin(db)
    await machines_svc.clear_broken(db, admin, machine_id)
    await db.commit()
    return core.redirect("fixed")


@router.post("/machines/{machine_id}/cancel")
async def cancel_session(db: Db, machine_id: int, reason: str = Form("")) -> Response:
    """Снять чужую работу. Причина обязательна: человек должен понять, за что."""
    reason = reason.strip()
    if not reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t.ERR_REASON_REQUIRED)

    admin = await core.acting_admin(db)
    result = await machines_svc.release(db, admin, machine_id, reason=reason)
    await db.commit()

    if result.owner_user_id is not None and result.owner_user_id != admin.id:
        await notify.send_to_user(
            db, result.owner_user_id, texts.work_cancelled_by_admin(result.machine_name, reason)
        )
    return core.redirect("cancelled")
