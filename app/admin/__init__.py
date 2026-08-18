"""Админка: ядро плюс разделы.

Снаружи недоступна: Caddy отдаёт на `/admin*` 404, заходить нужно через
SSH-туннель (см. DEPLOY.md). Вход по `ADMIN_SECRET`, а не по 4-значному PIN —
панель с правом снимать чужие работы не должна открываться перебором.

Панель собирается здесь и только здесь. `SECTIONS` — это одновременно порядок
пунктов в меню и список того, что вообще подключено: чтобы добавить раздел,
нужен файл в `sections/` с `SECTION` и `router` и одна строка в этом списке.
Автоматического поиска модулей по папке нет намеренно — порядок разделов должен
читаться глазами в одном месте, а не собираться из имён файлов.

Разделы разделены по частоте и по цене ошибки, и меню группирует их так же:

* «сейчас» — «Сводка» (оперативное: снять зависшую работу, вывести машину в
  обслуживание), «Брони», «Люди», «Журнал». Сюда
  заходят, когда что-то пошло не так, и застрявшее состояние чинится отсюда, а
  не походом в psql;
* «настройка» — «Помещения», «Оборудование», «Часы работы». Редкое, зато
  меняющее то, что видно на стене.

Ядро в `core.py`: что такое раздел, откуда берётся меню, кто проверяет доступ.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app.admin import core
from app.admin.sections import bookings, hours, log, machines, people, rooms, rules, summary
from app.api.render import templates

SECTIONS = [
    summary.SECTION,
    bookings.SECTION,
    people.SECTION,
    log.SECTION,
    rooms.SECTION,
    machines.SECTION,
    hours.SECTION,
    rules.SECTION,
]

# Меню рисуется из этого же списка: шаблон оболочки берёт его отсюда, а не из
# контекста каждой страницы — иначе новый раздел молча остался бы без пункта.
templates.env.globals["ADMIN_SECTIONS"] = SECTIONS
templates.env.globals["ADMIN_GROUP_NOW"] = core.GROUP_NOW
templates.env.globals["ADMIN_GROUP_SETUP"] = core.GROUP_SETUP

router = APIRouter()


@router.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
async def login_form(request: Request) -> Response:
    """Форма входа — единственная страница панели без проверки доступа.

    Поэтому она вне `build_router`: попади она внутрь, вход требовал бы входа.
    Проверяет секрет и выдаёт cookie обработчик в `api/auth.py`.
    """
    return templates.TemplateResponse(request, "admin/login.html", {})


router.include_router(core.build_router(SECTIONS))
