"""Экраны киоска.

iPad висит на стене постоянно, поэтому:

* главный экран доступен без входа — статусы видны всем;
* каждое действие требует PIN — вошедшего между запросами не помним (правило
  10): планшет общий, и следующий у экрана не должен ни ждать чужую сессию, ни
  занять принтер от чужого имени;
* всё помещается в один экран без скролла, цели не меньше 60 px;
* никаких внешних CDN: страница должна собираться из того, что отдал сервер.
"""

from datetime import UTC, datetime, time, timedelta

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.api.deps import Db, client_key, is_kiosk
from app.bot import notify, texts
from app.config import settings
from app.enums import PrinterStatus
from app.models import Printer, User
from app.services import auth
from app.services import board as board_svc
from app.services import printers as printers_svc
from app.services import queue as queue_svc
from app.services.errors import AuthFailed, PrinterNotAvailable

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _hhmm(value: datetime | None) -> str:
    return value.astimezone(settings.zone).strftime(t.TIME_FORMAT) if value else ""


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


templates.env.filters["hhmm"] = _hhmm
templates.env.filters["iso"] = _iso

# Надписи шаблонов доступны как `t.<ключ>`, строки для браузера — как `t_js`.
# Глобальные, а не в контексте: их ждёт base.html, который рисуется на каждый
# ответ, включая экран ошибки из main.py.
templates.env.globals["t"] = t.UI
templates.env.globals["t_js"] = t.JS

FLASH_MESSAGES = t.FLASH_KIOSK

NIGHT_UNTIL = time(9, 0)  # «ночь» — это печать до утра


async def build_board(db: AsyncSession) -> dict:
    """Данные главного экрана для шаблонов."""
    state = await board_svc.build(db)
    return {
        "printers": state.printers,
        "queue": state.queue,
        "free_count": state.free_count,
        "now": state.now,
    }


def duration_options(now: datetime) -> list[dict]:
    """Кнопки длительности. «Ночь» считается до 09:00, а не фиксированные 12 ч."""
    local = now.astimezone(settings.zone)
    morning = datetime.combine(local.date(), NIGHT_UNTIL, tzinfo=local.tzinfo)
    if morning <= local:
        morning += timedelta(days=1)
    night_minutes = int((morning - local).total_seconds() // 60)

    options = [
        {"label": label, "minutes": minutes} for minutes, label in t.DURATION_LABELS.items()
    ]
    if night_minutes >= printers_svc.MIN_DURATION_MINUTES:
        options.append({"label": t.DURATION_NIGHT, "minutes": night_minutes})
    return options


async def resolve_actor(request: Request, db: AsyncSession, pin: str) -> User:
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


def _done(flash: str) -> RedirectResponse:
    return RedirectResponse(f"/?flash={flash}", status_code=status.HTTP_303_SEE_OTHER)


# --- экраны ------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def board(request: Request, db: Db, flash: str = "") -> Response:
    context = await build_board(db)
    context["flash"] = FLASH_MESSAGES.get(flash)
    context["kiosk"] = is_kiosk(request)
    return templates.TemplateResponse(request, "kiosk.html", context)


@router.get("/partials/printers", response_class=HTMLResponse)
async def printers_partial(request: Request, db: Db) -> Response:
    """Кусок страницы, который сам перезапрашивается каждые 10 секунд."""
    context = await build_board(db)
    context["kiosk"] = is_kiosk(request)
    return templates.TemplateResponse(request, "_board.html", context)


@router.get("/occupy/{printer_id}", response_class=HTMLResponse)
async def occupy_form(request: Request, db: Db, printer_id: int) -> Response:
    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t.ERR_PRINTER_NOT_FOUND)

    # Тупиковую форму показывать нельзя: человек введёт PIN, выберет время и
    # только тогда узнает, что принтер занят.
    if printer.status == PrinterStatus.BROKEN:
        raise PrinterNotAvailable(t.ERR_PRINTER_BROKEN.format(printer=printer.name))
    if printer.status != PrinterStatus.FREE:
        raise PrinterNotAvailable(t.ERR_PRINTER_BUSY.format(printer=printer.name))

    return templates.TemplateResponse(
        request,
        "occupy.html",
        {
            "printer": printer,
            "durations": duration_options(datetime.now(UTC)),
        },
    )


@router.post("/occupy/{printer_id}")
async def occupy_action(
    request: Request,
    db: Db,
    printer_id: int,
    minutes: int = Form(...),
    pin: str = Form(""),
) -> Response:
    user = await resolve_actor(request, db, pin)
    result = await printers_svc.occupy(db, user, printer_id, minutes)
    await db.commit()
    await notify.send_to_user(
        db, user.id, texts.occupied(result.printer_name, result.eta_at, datetime.now(UTC))
    )
    return _done("occupied")


@router.get("/release/{printer_id}", response_class=HTMLResponse)
async def release_form(request: Request, db: Db, printer_id: int) -> Response:
    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t.ERR_PRINTER_NOT_FOUND)

    claimed = printer.status == PrinterStatus.DONE_WAIT
    title = t.CONFIRM_CLAIM_TITLE if claimed else t.CONFIRM_RELEASE_TITLE
    hint = t.CONFIRM_CLAIM_HINT if claimed else t.CONFIRM_RELEASE_HINT
    return templates.TemplateResponse(
        request,
        "confirm.html",
        {
            "title": title,
            "hint": hint,
            "subject": printer.name,
            "action": f"/release/{printer.id}",
            "submit": t.CONFIRM_RELEASE_SUBMIT,
        },
    )


@router.post("/release/{printer_id}")
async def release_action(
    request: Request, db: Db, printer_id: int, pin: str = Form("")
) -> Response:
    user = await resolve_actor(request, db, pin)
    result = await printers_svc.release(db, user, printer_id)
    await db.commit()

    # Правило 9: снять чужую деталь можно, но владелец должен об этом узнать.
    if result.owner_user_id is not None and result.owner_user_id != user.id:
        await notify.send_to_user(
            db, result.owner_user_id, texts.released_by_other(result.printer_name, user.name)
        )
    await notify.announce_offers(db, result.offers)
    return _done("released")


@router.get("/queue/join", response_class=HTMLResponse)
async def queue_join_form(request: Request, db: Db) -> Response:
    return templates.TemplateResponse(
        request,
        "confirm.html",
        {
            "title": t.CONFIRM_QUEUE_JOIN_TITLE,
            "hint": t.CONFIRM_QUEUE_JOIN_HINT,
            "subject": t.CONFIRM_QUEUE_JOIN_SUBJECT,
            "action": "/queue/join",
            "submit": t.CONFIRM_QUEUE_JOIN_SUBMIT,
        },
    )


@router.post("/queue/join")
async def queue_join_action(request: Request, db: Db, pin: str = Form("")) -> Response:
    user = await resolve_actor(request, db, pin)
    result = await queue_svc.join(db, user.id)
    await db.commit()
    await notify.announce_offers(db, result.offers)
    return _done("queued")


@router.get("/queue/leave", response_class=HTMLResponse)
async def queue_leave_form(request: Request, db: Db) -> Response:
    return templates.TemplateResponse(
        request,
        "confirm.html",
        {
            "title": t.CONFIRM_QUEUE_LEAVE_TITLE,
            "hint": t.CONFIRM_QUEUE_LEAVE_HINT,
            "subject": t.CONFIRM_QUEUE_LEAVE_SUBJECT,
            "action": "/queue/leave",
            "submit": t.CONFIRM_QUEUE_LEAVE_SUBMIT,
        },
    )


@router.post("/queue/leave")
async def queue_leave_action(request: Request, db: Db, pin: str = Form("")) -> Response:
    user = await resolve_actor(request, db, pin)
    result = await queue_svc.leave(db, user.id)
    await db.commit()
    await notify.announce_offers(db, result.offers)
    return _done("left")


@router.get("/offline", response_class=HTMLResponse, include_in_schema=False)
async def offline(request: Request) -> Response:
    """Отдаётся service worker-ом, когда сервер недоступен."""
    return templates.TemplateResponse(request, "offline.html", {})


@router.get("/sw.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    """Отдаём с корня: из /static/ service worker не смог бы контролировать всю страницу."""
    return FileResponse("app/static/sw.js", media_type="application/javascript")
