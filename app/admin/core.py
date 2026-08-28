"""Ядро админки: из чего состоит раздел и что у всех разделов общее.

Раздел (`Section`) — это страница в меню слева и роутер под ней. Модуль в
`app/admin/sections/` объявляет свой `SECTION` и свой `router`, а список в
`app/admin/__init__.py` собирает их в одну панель. Меню рисуется из этого
списка, а не из руками написанного HTML: новый раздел появляется в меню сам,
и забыть дописать туда ссылку невозможно.

Общее вынесено сюда, потому что каждая копия — это место, где однажды забудут:

* **доступ.** `require_admin` висит на родительском роутере, а не на каждом
  обработчике. Забытая проверка на одном POST открывает наружу право снимать
  чужие работы, и заметить это по коду нельзя — роут выглядит как соседние;
* **редирект с плашкой.** После действия всегда 303 на страницу раздела, иначе
  F5 повторяет POST. Ключ плашки проверяется по словарю: незнакомый ключ
  означает опечатку, и лучше увидеть её пустой плашкой, чем ключом на экране;
* **от чьего имени.** `ADMIN_SECRET` — право оператора, а не учётная запись,
  но доменные функции требуют пользователя, чтобы записать «кто снял».

Чего здесь нет намеренно: автоматического поиска модулей по папке. Порядок
разделов в меню — это порядок списка в `__init__.py`, и он должен читаться
глазами в одном месте, а не собираться из имён файлов.
"""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.api.deps import require_admin
from app.api.render import templates
from app.models import User

PREFIX = "/admin"

# Группы меню. Разделены по тому, зачем сюда заходят: «сейчас» — когда что-то
# пошло не так и это надо починить, «настройка» — редкое, зато меняющее то, что
# видно на стене.
GROUP_NOW = "now"
GROUP_SETUP = "setup"

FLASH_MESSAGES = t.FLASH_ADMIN


@dataclass(frozen=True)
class Section:
    """Раздел админки: пункт меню и роутер под ним.

    `slug` пустой — это «Сводка», главная страница панели (`/admin`).
    `icon` — имя макроса в templates/admin/_icons.html; иконки нарисованы там
    же, потому что внешних зависимостей в проекте нет (см. app.css).
    """

    slug: str
    title: str
    icon: str
    router: APIRouter
    group: str = GROUP_NOW
    # Заголовок страницы, если он длиннее пункта меню: «Журнал» в меню и
    # «Последние события» над списком — одно и то же, но в разных местах.
    heading: str = ""

    @property
    def path(self) -> str:
        return PREFIX if not self.slug else f"{PREFIX}/{self.slug}"


def section_router() -> APIRouter:
    """Роутер раздела. Адреса в нём пишутся от корня панели: `/rooms`, `/hours`.

    Префикс общий, а не по слагу раздела, потому что раздел не всегда владеет
    одной веткой адресов: «Люди» живут на `/admin/people`, а действия над ними —
    на `/admin/users/...` (раздел называется по тому, что видит оператор, а
    таблица — по тому, что лежит в базе). Полный адрес виден прямо в декораторе,
    и искать его по префиксам не нужно.
    """
    return APIRouter(prefix=PREFIX)


def build_router(sections: list[Section]) -> APIRouter:
    """Собрать панель: все разделы под одной проверкой доступа.

    Форма входа сюда не входит — она обязана открываться без входа, и лежит
    отдельным публичным роутом в `app/admin/__init__.py`.
    """
    panel = APIRouter(dependencies=[Depends(require_admin)])
    for section in sections:
        panel.include_router(section.router)
    return panel


def render(
    request: Request,
    section: "Section",
    template: str,
    context: dict | None = None,
    flash: str = "",
) -> Response:
    """Отрисовать страницу раздела.

    Раздел передаётся сюда, а не вычисляется шаблоном из адреса: из него берутся
    и заголовок страницы, и подсветка пункта в меню, и `<title>` вкладки. Меню
    целиком шаблон берёт из глобальных Jinja (`ADMIN_SECTIONS`) — передавать
    список в каждый контекст значило бы, что новый раздел молча теряет пункт.
    """
    return templates.TemplateResponse(
        request,
        template,
        {
            "section": section,
            "flash": FLASH_MESSAGES.get(flash),
            "from_miniapp": bool(getattr(request.state, "admin_from_app", False)),
            **(context or {}),
        },
    )


def confirm_delete(
    request: Request,
    section: "Section",
    name: str,
    fallout,
    action: str,
    hint: str = "",
) -> Response:
    """Экран «удалить навсегда?» — один на все разделы.

    Удаление здесь ничего не запрещает, но и не случается по одному нажатию:
    сначала показывается, что именно уедет вместе с объектом. Это единственная
    защита, которая тут уместна, — скрытая кнопка учит не читать сообщения, а
    отказ «нельзя, есть история» оставляет тестовые данные в базе навсегда.

    Подтверждение — отдельная страница, а не окно браузера: скриптов на
    страницах админки нет (см. base.html), а `confirm()` — это скрипт.
    """
    return render(
        request,
        section,
        "admin/confirm_delete.html",
        {
            "name": name,
            "fallout": fallout,
            "action": action,
            "back": section.path,
            # Что делать вместо удаления, если история нужна. Есть не у
            # каждого объекта: человека «в обслуживание» не выведешь.
            "hint": hint,
        },
    )


def redirect(flash: str, to: str = PREFIX) -> RedirectResponse:
    """303 после действия: F5 не должен повторять POST."""
    separator = "&" if "?" in to else "?"
    return RedirectResponse(f"{to}{separator}flash={flash}", status_code=status.HTTP_303_SEE_OTHER)


async def acting_admin(db: AsyncSession) -> User:
    """От чьего имени пишутся действия админки.

    Личности за секретом нет, поэтому берём первого админа в базе — он попадёт
    в журнал как тот, кто снял работу.
    """
    admin = await db.scalar(select(User).where(User.is_admin.is_(True)).order_by(User.id))
    if admin is None:
        raise HTTPException(status.HTTP_409_CONFLICT, t.ERR_NO_ADMIN_IN_DB)
    return admin
