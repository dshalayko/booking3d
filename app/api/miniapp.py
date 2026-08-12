"""Роуты Telegram Mini App — те же экраны с телефона.

Открывается из бота командой /book или кнопкой меню. Telegram грузит нашу же
страницу в свой встроенный браузер и отдаёт ей `initData` — подпись открытия на
токене бота. Страница `/app` пересылает её на `/app/session`, сервер проверяет
подпись (services/telegram.py) и ставит cookie на месяц. Дальше приложение
работает обычными серверными формами, без SPA: те же шаблоны, что у киоска, но
без клавиатуры PIN — личность уже подтверждена Telegram.

Почему проверка происходит через отдельную страницу-бутстрап: `initData` живёт в
JavaScript-объекте `window.Telegram.WebApp`, серверу его нужно передать явно.
Один лишний переход человек не замечает, зато на всех остальных экранах нет ни
одной строки клиентского состояния.

Читать доску и расписание можно и без сессии — как и на открытом сайте. Сессия
нужна только для действий и для экрана «мои брони», и GET-экраны при её
отсутствии сами открывают бутстрап с адресом возврата.

`MINIAPP_OPEN_ACCESS=true` заменяет подпись на выбор человека из списка — это
режим для проверки брон до того, как заведён бот и получен сертификат. Сама
проверка подписи при этом не отключается: непустой `initData` разбирается как
обычно, и подделка отклоняется даже с включённым флагом. Иначе тесты на подпись
зеленели бы, ничего не проверяя, — та же ловушка, что с `KIOSK_OPEN_ACCESS`.
"""

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.api import screens
from app.api.deps import Db, cookie_params
from app.api.render import templates
from app.api.screens import APP
from app.config import settings
from app.models import User
from app.services import auth, telegram
from app.services.errors import AppSessionRequired

router = APIRouter(prefix="/app")

# Куда пускаем возвращаться после проверки подписи. Открытый список, а не любой
# путь из формы: иначе `next` превращается в открытый редирект на чужой сайт.
SAFE_NEXT_PREFIX = "/app"


def _safe_next(value: str) -> str:
    return value if value.startswith(SAFE_NEXT_PREFIX) else f"{SAFE_NEXT_PREFIX}/"


async def viewer(request: Request, db: AsyncSession) -> User | None:
    """Кто смотрит, если сессия есть. Без сессии — None, это не ошибка."""
    user_id = auth.app_session_user_id(request.cookies.get(auth.APP_COOKIE))
    if user_id is None:
        return None
    return await db.scalar(select(User).where(User.id == user_id))


async def actor(request: Request, db: AsyncSession) -> User:
    """Кто действует. Без живой сессии действий нет."""
    person = await viewer(request, db)
    if person is None:
        raise AppSessionRequired(t.ERR_APP_SESSION_REQUIRED)
    return person


async def _bootstrap(
    request: Request, db: AsyncSession, next_path: str = f"{SAFE_NEXT_PREFIX}/"
) -> Response:
    """Страница, которая отдаёт подпись Telegram серверу и уходит дальше.

    Вне Telegram подписи нет, и страница объясняет, что приложение открывают из
    бота. В тестовом режиме вместо объяснения — список людей: войти можно любым,
    подпись не спрашивается.
    """
    people = []
    if settings.miniapp_open_access:
        people = list((await db.scalars(select(User).order_by(User.name))).all())

    return templates.TemplateResponse(
        request,
        "app.html",
        {
            "next": next_path,
            "open_access": settings.miniapp_open_access,
            "people": people,
            **APP.context,
        },
    )


# --- вход --------------------------------------------------------------------


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse)
async def entry(request: Request, db: Db, next: str = "", flash: str = "") -> Response:
    """Точка входа и главный экран приложения.

    С живой сессией показывает доску — сюда же возвращают все действия. Без
    сессии показывает бутстрап, который проверит подпись и вернёт обратно.
    """
    if await viewer(request, db) is not None and not next:
        return await screens.board_page(request, db, APP, flash)
    return await _bootstrap(request, db, _safe_next(next or f"{SAFE_NEXT_PREFIX}/"))


@router.post("/session")
async def open_session(
    request: Request,
    db: Db,
    init_data: str = Form(""),
    next: str = Form(""),
    as_user_id: int | None = Form(None),
) -> Response:
    """Обменять подпись Telegram на сессию приложения.

    Непустой `initData` проверяется всегда, даже в тестовом режиме: подделанная
    подпись должна отклоняться независимо от флагов, иначе проверка перестаёт
    что-либо значить. Тестовый вход — это отдельная ветка для случая, когда
    подписи нет вовсе.
    """
    if init_data or not settings.miniapp_open_access:
        chat_id = telegram.check_init_data(init_data)
        person = await db.scalar(select(User).where(User.tg_chat_id == chat_id))
    else:
        person = await _test_person(db, as_user_id)

    if person is None:
        # Логин спрашивает бот (/start) — приложение не выдаёт ни логинов, ни
        # PIN-ов, иначе появился бы второй путь регистрации со своими правилами.
        return templates.TemplateResponse(
            request, "app_unknown.html", {**APP.context}, status_code=status.HTTP_403_FORBIDDEN
        )

    response = RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        auth.APP_COOKIE,
        auth.issue_app_session(person.id),
        **cookie_params(auth.APP_SESSION_TTL),
    )
    return response


# --- доска -------------------------------------------------------------------


@router.get("/partials/board", response_class=HTMLResponse)
async def board_partial(request: Request, db: Db) -> Response:
    return await screens.board_partial(request, db, APP)


# --- занять / освободить -----------------------------------------------------


@router.get("/occupy/{machine_id}", response_class=HTMLResponse)
async def occupy_form(request: Request, db: Db, machine_id: int) -> Response:
    return await screens.occupy_page(request, db, APP, machine_id)


@router.post("/occupy/{machine_id}")
async def occupy_action(
    request: Request, db: Db, machine_id: int, minutes: int = Form(...)
) -> Response:
    user = await actor(request, db)
    return await screens.do_occupy(db, APP, user, machine_id, minutes)


@router.get("/release/{machine_id}", response_class=HTMLResponse)
async def release_form(request: Request, db: Db, machine_id: int) -> Response:
    return await screens.release_page(request, db, APP, machine_id)


@router.post("/release/{machine_id}")
async def release_action(request: Request, db: Db, machine_id: int) -> Response:
    user = await actor(request, db)
    return await screens.do_release(db, APP, user, machine_id)


# --- очередь -----------------------------------------------------------------


@router.get("/queue/join/{kind}", response_class=HTMLResponse)
async def queue_join_form(request: Request, db: Db, kind: str) -> Response:
    return await screens.queue_join_page(request, db, APP, kind)


@router.post("/queue/join/{kind}")
async def queue_join_action(request: Request, db: Db, kind: str) -> Response:
    user = await actor(request, db)
    return await screens.do_queue_join(db, APP, user, kind)


@router.get("/queue/leave", response_class=HTMLResponse)
async def queue_leave_form(request: Request) -> Response:
    return await screens.queue_leave_page(request, APP)


@router.post("/queue/leave")
async def queue_leave_action(request: Request, db: Db) -> Response:
    user = await actor(request, db)
    return await screens.do_queue_leave(db, APP, user)


# --- расписание и брони ------------------------------------------------------


@router.get("/schedule/{kind}", response_class=HTMLResponse)
async def schedule_screen(request: Request, db: Db, kind: str, date: str = "") -> Response:
    """Здесь свои брони подсвечены: в отличие от киоска, известно, кто смотрит."""
    return await screens.schedule_page(
        request, db, APP, kind, date, viewer=await viewer(request, db)
    )


@router.get("/book/{machine_id}", response_class=HTMLResponse)
async def book_form(request: Request, db: Db, machine_id: int, start: str = "") -> Response:
    return await screens.book_page(request, db, APP, machine_id, start)


@router.post("/book/{machine_id}")
async def book_action(
    request: Request,
    db: Db,
    machine_id: int,
    start: str = Form(...),
    minutes: int = Form(...),
) -> Response:
    user = await actor(request, db)
    return await screens.do_book(db, APP, user, machine_id, start, minutes)


@router.get("/booking/{reservation_id}/cancel", response_class=HTMLResponse)
async def booking_cancel_form(request: Request, db: Db, reservation_id: int) -> Response:
    return await screens.booking_cancel_page(request, db, APP, reservation_id)


@router.post("/booking/{reservation_id}/cancel")
async def booking_cancel_action(request: Request, db: Db, reservation_id: int) -> Response:
    user = await actor(request, db)
    return await screens.do_booking_cancel(db, APP, user, reservation_id)


@router.get("/my", response_class=HTMLResponse)
async def my_screen(request: Request, db: Db) -> Response:
    """Мои брони. Без сессии — бутстрап, который вернёт сюда же."""
    person = await viewer(request, db)
    if person is None:
        return await _bootstrap(request, db, f"{SAFE_NEXT_PREFIX}/my")
    return await screens.my_page(request, db, APP, person)


async def _test_person(db: AsyncSession, user_id: int | None) -> User | None:
    """Кто вошёл в тестовом режиме. Без номера — первый по имени.

    Отдельной функцией, чтобы ветка «без подписи» была видна в одном месте и её
    нельзя было случайно позвать из обычного пути.
    """
    if user_id is not None:
        return await db.get(User, user_id)
    return await db.scalar(select(User).order_by(User.name).limit(1))
