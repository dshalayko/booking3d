"""«Люди» — все, кто есть в системе.

Обычно человек заводится сам, командой /start у бота. Здесь его можно завести
руками и удалить — и то, и другое нужно ровно для двух случаев: тестовые
учётки, у которых Telegram нет вовсе, и человек, который до бота не дошёл.
Плюс то, что человек не починит сам: опечатку в логине (бот второй раз логин не
спрашивает) и забытый PIN.

Адреса действий остались `/admin/users/...`: раздел называется по тому, что
видит оператор, а таблица — по тому, что лежит в базе.
"""

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.bot import notify, texts
from app.models import User
from app.services import auth, purge
from app.services import users as users_svc
from app.services.errors import UserNotFound

router = core.section_router()

SECTION = core.Section(
    slug="people",
    title=t.UI["admin_nav_people"],
    icon="people",
    router=router,
    group=core.GROUP_NOW,
)


@router.get("/people", response_class=HTMLResponse)
async def page(request: Request, db: Db, flash: str = "") -> Response:
    return core.render(
        request, SECTION, "admin/people.html",
        {"users": await users_svc.list_people(db)}, flash
    )


@router.post("/users")
async def add(
    request: Request,
    db: Db,
    login: str = Form(""),
    tg_chat_id: int = Form(0),
    pin: str = Form(""),
) -> Response:
    admin = await core.acting_admin(db, request)
    await users_svc.create(db, admin, login, tg_chat_id, pin)
    await db.commit()
    return core.redirect("person_added", SECTION.path)


@router.post("/users/{user_id}/name")
async def rename(db: Db, user_id: int, name: str = Form("")) -> Response:
    """Поправить логин, введённый при регистрации с опечаткой.

    Прошлые записи в журнале тоже начнут показывать новый логин: журнал
    собирается из таблиц по `users.name`, отдельных копий имени нигде нет. Для
    исправления опечатки это ровно то, что нужно.
    """
    person = await _person(db, user_id)
    previous = await users_svc.rename(db, person, name)
    if previous == person.name:
        return core.redirect("renamed", SECTION.path)

    new_name = person.name
    await db.commit()
    # Человек должен знать: под этим логином его видно на планшете, и в чужом
    # переименовании ему проще заметить ошибку, чем админу.
    await notify.send_to_user(db, user_id, texts.name_changed(previous, new_name))
    return core.redirect("renamed", SECTION.path)


@router.post("/users/{user_id}/pin")
async def reset_pin(db: Db, user_id: int) -> Response:
    """Сбросить PIN человеку, который его забыл и не может дойти до бота."""
    person = await _person(db, user_id)
    pin = await auth.assign_pin(db, person)
    await db.commit()
    # PIN уходит только в Telegram: в редиректе он попал бы в логи и историю.
    await notify.send_to_user(db, user_id, texts.pin_changed(pin))
    return core.redirect("pin_reset", SECTION.path)


@router.get("/users/{user_id}/delete", response_class=HTMLResponse)
async def confirm_delete(request: Request, db: Db, user_id: int) -> Response:
    """Что уедет вместе с человеком: работы, брони и старые записи очереди.

    Чужие работы, которые он закрыл, остаются — у них только пропадёт подпись
    «освободил такой-то» (см. services/purge.py).
    """
    person = await _person(db, user_id)
    return core.confirm_delete(
        request,
        SECTION,
        person.name,
        await purge.person_fallout(db, user_id),
        f"/admin/users/{user_id}/delete",
    )


@router.post("/users/{user_id}/delete")
async def delete(request: Request, db: Db, user_id: int) -> Response:
    """Удалить человека.

    Подтверждения от формы здесь не требуется: у человека нет «пустого»
    состояния, в котором удаление безопасно, — работы и брони есть почти у
    каждого. Единственный отказ — на последнем админе (см. services/purge.py).
    """
    admin = await core.acting_admin(db, request)
    await purge.purge_person(db, admin, user_id)
    await db.commit()
    return core.redirect("person_removed", SECTION.path)


@router.post("/users/{user_id}/admin")
async def make_admin(request: Request, db: Db, user_id: int) -> Response:
    admin = await core.acting_admin(db, request)
    await users_svc.set_admin(db, admin, user_id, True)
    await db.commit()
    return core.redirect("admin_granted", SECTION.path)


@router.post("/users/{user_id}/admin/remove")
async def remove_admin(request: Request, db: Db, user_id: int) -> Response:
    admin = await core.acting_admin(db, request)
    await users_svc.set_admin(db, admin, user_id, False)
    await db.commit()
    return core.redirect("admin_revoked", SECTION.path)


async def _person(db: Db, user_id: int) -> User:
    person = await db.get(User, user_id)
    if person is None:
        raise UserNotFound(t.ERR_USER_NOT_FOUND)
    return person
