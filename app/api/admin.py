"""Админка.

Снаружи недоступна: Caddy отдаёт на `/admin*` 404, заходить нужно через
SSH-туннель (см. DEPLOY.md). Вход по `ADMIN_SECRET`, а не по 4-значному PIN —
панель с правом снимать чужие работы не должна открываться перебором.

Две вкладки, потому что задачи разные по частоте и по цене ошибки:

* «Сводка» — оперативное: снять зависшую работу, вывести машину в
  обслуживание, убрать человека из очереди. Сюда заходят, когда что-то пошло
  не так, и застрявшее состояние чинится отсюда, а не походом в psql;
* «Оборудование» — состав парка: завести машину, переименовать, удалить.
  Действия редкие, зато меняют то, что видно на стене, поэтому им отдельная
  страница, а не ещё один блок среди срочных кнопок.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.api.deps import Db, require_admin
from app.api.kiosk import templates
from app.bot import notify, texts
from app.enums import MachineKind, MachineStatus
from app.models import User
from app.services import activity as activity_svc
from app.services import auth
from app.services import board as board_svc
from app.services import machines as machines_svc
from app.services import queue as queue_svc
from app.services import users as users_svc

router = APIRouter(prefix="/admin")

FLASH_MESSAGES = t.FLASH_ADMIN


async def acting_admin(db: AsyncSession) -> User:
    """От чьего имени пишутся действия админки.

    `ADMIN_SECRET` — это право оператора, а не учётная запись, но доменные
    функции требуют пользователя, чтобы записать «кто снял». Личности за
    секретом нет, поэтому берём первого админа в базе.
    """
    admin = await db.scalar(select(User).where(User.is_admin.is_(True)).order_by(User.id))
    if admin is None:
        raise HTTPException(status.HTTP_409_CONFLICT, t.ERR_NO_ADMIN_IN_DB)
    return admin


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_form(request: Request) -> Response:
    return templates.TemplateResponse(request, "admin_login.html", {})


@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def dashboard(request: Request, db: Db, flash: str = "") -> Response:
    board = await board_svc.build(db)
    users = list((await db.scalars(select(User).order_by(User.name))).all())
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "board": board,
            "users": users,
            "events": await activity_svc.recent(db),
            "flash": FLASH_MESSAGES.get(flash),
            "MachineStatus": MachineStatus,
        },
    )


# --- оперативное -------------------------------------------------------------


@router.post("/machines/{machine_id}/break", dependencies=[Depends(require_admin)])
async def break_machine(
    request: Request, db: Db, machine_id: int, note: str = Form("")
) -> Response:
    admin = await acting_admin(db)
    result = await machines_svc.set_broken(db, admin, machine_id, note=note.strip() or None)
    await db.commit()

    if result.owner_user_id is not None:
        await notify.send_to_user(
            db,
            result.owner_user_id,
            texts.work_cancelled_by_admin(result.machine_name, note.strip() or None),
        )
    return _back("broken")


@router.post("/machines/{machine_id}/fix", dependencies=[Depends(require_admin)])
async def fix_machine(request: Request, db: Db, machine_id: int) -> Response:
    admin = await acting_admin(db)
    result = await machines_svc.clear_broken(db, admin, machine_id)
    await db.commit()
    await notify.announce_offers(db, result.offers)
    return _back("fixed")


@router.post("/machines/{machine_id}/cancel", dependencies=[Depends(require_admin)])
async def cancel_session(
    request: Request, db: Db, machine_id: int, reason: str = Form("")
) -> Response:
    """Снять чужую работу. Причина обязательна: человек должен понять, за что."""
    reason = reason.strip()
    if not reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t.ERR_REASON_REQUIRED)

    admin = await acting_admin(db)
    result = await machines_svc.release(db, admin, machine_id, reason=reason)
    await db.commit()

    if result.owner_user_id is not None and result.owner_user_id != admin.id:
        await notify.send_to_user(
            db, result.owner_user_id, texts.work_cancelled_by_admin(result.machine_name, reason)
        )
    await notify.announce_offers(db, result.offers)
    return _back("cancelled")


@router.post("/queue/{user_id}/remove", dependencies=[Depends(require_admin)])
async def remove_from_queue(request: Request, db: Db, user_id: int) -> Response:
    result = await queue_svc.leave(db, user_id, now=datetime.now(UTC))
    await db.commit()
    await notify.send_to_user(db, user_id, texts.removed_from_queue())
    await notify.announce_offers(db, result.offers)
    return _back("removed")


# --- состав парка ------------------------------------------------------------


@router.get("/machines", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
async def machines_page(request: Request, db: Db, flash: str = "") -> Response:
    """Вкладка «Оборудование»: что стоит в мастерской и что с этим можно сделать.

    Рядом с каждой машиной показано, сколько за ней записей в журнале: удалить
    можно только машину без истории, и лучше объяснить это заранее, чем отказом
    после нажатия.
    """
    park = await machines_svc.list_machines(db)
    rows = [
        {"machine": machine, "usage": await machines_svc.usage(db, machine.id)}
        for machine in park
    ]
    return templates.TemplateResponse(
        request,
        "admin_machines.html",
        {
            "groups": [
                {"kind": kind, "rows": [row for row in rows if row["machine"].kind == kind]}
                for kind in MachineKind
            ],
            "flash": FLASH_MESSAGES.get(flash),
        },
    )


@router.post("/machines", dependencies=[Depends(require_admin)])
async def add_machine(
    request: Request, db: Db, name: str = Form(""), kind: str = Form("")
) -> Response:
    admin = await acting_admin(db)
    await machines_svc.create(db, admin, name, kind)
    await db.commit()
    return _back("machine_added", page="/admin/machines")


@router.post("/machines/{machine_id}/name", dependencies=[Depends(require_admin)])
async def rename_machine(
    request: Request, db: Db, machine_id: int, name: str = Form("")
) -> Response:
    admin = await acting_admin(db)
    await machines_svc.rename(db, admin, machine_id, name)
    await db.commit()
    return _back("machine_renamed", page="/admin/machines")


@router.post("/machines/{machine_id}/delete", dependencies=[Depends(require_admin)])
async def delete_machine(request: Request, db: Db, machine_id: int) -> Response:
    admin = await acting_admin(db)
    await machines_svc.remove(db, admin, machine_id)
    await db.commit()
    return _back("machine_removed", page="/admin/machines")


# --- люди --------------------------------------------------------------------


@router.post("/users/{user_id}/name", dependencies=[Depends(require_admin)])
async def rename_user(db: Db, user_id: int, name: str = Form("")) -> Response:
    """Поправить логин, введённый при регистрации с опечаткой.

    Прошлые записи в журнале тоже начнут показывать новый логин: журнал
    собирается из таблиц по `users.name`, отдельных копий имени нигде нет. Для
    исправления опечатки это ровно то, что нужно.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t.ERR_USER_NOT_FOUND)

    previous = await users_svc.rename(db, user, name)
    if previous == user.name:
        return _back("renamed")

    new_name = user.name
    await db.commit()
    # Человек должен знать: под этим логином его видно на планшете, и в чужом
    # переименовании ему проще заметить ошибку, чем админу.
    await notify.send_to_user(db, user_id, texts.name_changed(previous, new_name))
    return _back("renamed")


@router.post("/users/{user_id}/pin", dependencies=[Depends(require_admin)])
async def reset_pin(db: Db, user_id: int) -> Response:
    """Сбросить PIN человеку, который его забыл и не может дойти до бота."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t.ERR_USER_NOT_FOUND)

    pin = await auth.assign_pin(db, user)
    await db.commit()
    # PIN уходит только в Telegram: в редиректе он попал бы в логи и историю.
    await notify.send_to_user(db, user_id, texts.pin_changed(pin))
    return _back("pin_reset")


def _back(flash: str, page: str = "/admin") -> RedirectResponse:
    return RedirectResponse(f"{page}?flash={flash}", status_code=status.HTTP_303_SEE_OTHER)
