"""Роуты киоска — планшета, который висит на стене помещения.

Планшет привязан к одному помещению: комната записана в его device-cookie при
настройке (`/kiosk/enroll`, api/auth.py), и главный экран показывает только её.
Так и висит: один планшет — одна комната, никаких переключателей, которые
кто-нибудь оставит в чужом помещении. Планшет, зарегистрированный до появления
комнат, показывает их список и просит выбрать — отказывать ему было бы хуже.

iPad висит там постоянно, поэтому:

* главный экран доступен без входа — статусы видны всем;
* каждое действие требует PIN — вошедшего между запросами не помним (правило
  10): планшет общий, и следующий у экрана не должен ни ждать чужую сессию, ни
  занять машину от чужого имени;
* всё помещается в один экран без скролла, цели не меньше 60 px;
* никаких внешних CDN: страница должна собираться из того, что отдал сервер.

Сами экраны лежат в api/screens.py — те же самые показывает Mini App. Здесь
остаётся только то, что отличает киоск: под каждым действием вводится PIN.
"""

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, Response

from app import texts as t
from app.api import screens
from app.api.deps import Db, client_key, is_kiosk, kiosk_room_id
from app.api.render import templates
from app.api.screens import KIOSK, duration_options  # noqa: F401  (зовут тесты)
from app.models import Room, User
from app.services import auth
from app.services.errors import AuthFailed

router = APIRouter()


async def resolve_actor(request: Request, db: Db, pin: str) -> User:
    """Кто выполняет действие. PIN обязателен под каждым."""
    if not is_kiosk(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t.ERR_KIOSK_ONLY)

    key = client_key(request)
    auth.pin_limiter.ensure_allowed(key)
    try:
        user = await auth.user_by_pin(db, pin or "")
    except AuthFailed:
        auth.pin_limiter.register_failure(key)
        raise
    auth.pin_limiter.reset(key)
    return user


# --- доска -------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def board(request: Request, db: Db, flash: str = "") -> Response:
    """Доска помещения этого планшета.

    Планшет, зарегистрированный без комнаты (например, ещё до появления
    помещений), и любой посторонний браузер видят здесь первое помещение: экрана
    «все помещения» в системе нет, у каждой комнаты свой адрес.

    Помещение могли и удалить — закрылась мастерская, а планшет остался висеть.
    Тогда экран показывает первое из оставшихся, а не пятисотую: планшет на стене
    никто не перезагружает по звонку, и ошибка на нём живёт до следующего обхода.
    """
    room_id = kiosk_room_id(request)
    if room_id is None or await db.get(Room, room_id) is None:
        room_id = await screens.default_room_id(db)
    return await screens.board_page(request, db, KIOSK, room_id, flash)


@router.get("/room/{room_id}", response_class=HTMLResponse)
async def room_board(request: Request, db: Db, room_id: int, flash: str = "") -> Response:
    """Доска указанного помещения — постоянный адрес комнаты.

    Его вбивают в планшет, на него ведёт кнопка из админки, по нему же смотрят
    статусы с компьютера: доска открыта всем и без входа.
    """
    return await screens.board_page(request, db, KIOSK, room_id, flash)


@router.get("/partials/board/{room_id}", response_class=HTMLResponse)
async def board_partial(request: Request, db: Db, room_id: int) -> Response:
    return await screens.board_partial(request, db, KIOSK, room_id)


# --- занять / освободить -----------------------------------------------------


@router.get("/occupy/{machine_id}", response_class=HTMLResponse)
async def occupy_form(request: Request, db: Db, machine_id: int) -> Response:
    return await screens.occupy_page(request, db, KIOSK, machine_id)


@router.post("/occupy/{machine_id}")
async def occupy_action(
    request: Request,
    db: Db,
    machine_id: int,
    minutes: int = Form(...),
    pin: str = Form(""),
) -> Response:
    user = await resolve_actor(request, db, pin)
    return await screens.do_occupy(db, KIOSK, user, machine_id, minutes)


@router.get("/release/{machine_id}", response_class=HTMLResponse)
async def release_form(request: Request, db: Db, machine_id: int) -> Response:
    return await screens.release_page(request, db, KIOSK, machine_id)


@router.post("/release/{machine_id}")
async def release_action(
    request: Request, db: Db, machine_id: int, pin: str = Form("")
) -> Response:
    user = await resolve_actor(request, db, pin)
    return await screens.do_release(db, KIOSK, user, machine_id)


# --- очередь -----------------------------------------------------------------


@router.get("/queue/join/{room_id}/{kind}", response_class=HTMLResponse)
async def queue_join_form(request: Request, db: Db, room_id: int, kind: str) -> Response:
    return await screens.queue_join_page(request, db, KIOSK, room_id, kind)


@router.post("/queue/join/{room_id}/{kind}")
async def queue_join_action(
    request: Request, db: Db, room_id: int, kind: str, pin: str = Form("")
) -> Response:
    user = await resolve_actor(request, db, pin)
    return await screens.do_queue_join(db, KIOSK, user, room_id, kind)


@router.get("/queue/leave/{room_id}", response_class=HTMLResponse)
async def queue_leave_form(request: Request, db: Db, room_id: int) -> Response:
    return await screens.queue_leave_page(request, db, KIOSK, room_id)


@router.post("/queue/leave/{room_id}")
async def queue_leave_action(
    request: Request, db: Db, room_id: int, pin: str = Form("")
) -> Response:
    user = await resolve_actor(request, db, pin)
    return await screens.do_queue_leave(db, KIOSK, user, room_id)


# --- расписание и брони ------------------------------------------------------


@router.get("/schedule/{room_id}/{kind}", response_class=HTMLResponse)
async def schedule_screen(
    request: Request, db: Db, room_id: int, kind: str, date: str = ""
) -> Response:
    """Расписание одного типа в помещении. Своих брон киоск не подсвечивает: кто
    перед экраном, известно только после ввода PIN."""
    return await screens.schedule_page(request, db, KIOSK, room_id, kind, date)


@router.get("/book/{machine_id}", response_class=HTMLResponse)
async def book_form(request: Request, db: Db, machine_id: int, start: str = "") -> Response:
    return await screens.book_page(request, db, KIOSK, machine_id, start)


@router.post("/book/{machine_id}")
async def book_action(
    request: Request,
    db: Db,
    machine_id: int,
    start: str = Form(...),
    minutes: int = Form(...),
    pin: str = Form(""),
) -> Response:
    user = await resolve_actor(request, db, pin)
    return await screens.do_book(db, KIOSK, user, machine_id, start, minutes)


@router.get("/booking/{reservation_id}/cancel", response_class=HTMLResponse)
async def booking_cancel_form(request: Request, db: Db, reservation_id: int) -> Response:
    return await screens.booking_cancel_page(request, db, KIOSK, reservation_id)


@router.post("/booking/{reservation_id}/cancel")
async def booking_cancel_action(
    request: Request, db: Db, reservation_id: int, pin: str = Form("")
) -> Response:
    user = await resolve_actor(request, db, pin)
    return await screens.do_booking_cancel(db, KIOSK, user, reservation_id)


# --- служебное ---------------------------------------------------------------


@router.get("/offline", response_class=HTMLResponse, include_in_schema=False)
async def offline(request: Request) -> Response:
    """Отдаётся service worker-ом, когда сервер недоступен."""
    return templates.TemplateResponse(request, "offline.html", {})


@router.get("/sw.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    """Отдаём с корня: из /static/ service worker не смог бы контролировать всю страницу."""
    return FileResponse("app/static/sw.js", media_type="application/javascript")
