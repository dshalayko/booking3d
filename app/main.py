import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import texts as t
from app.api import admin as admin_routes
from app.api import auth as auth_routes
from app.api import kiosk as kiosk_routes
from app.api.kiosk import templates
from app.bot import notify
from app.bot.bot import attach_notifier, build_bot, start_polling
from app.config import settings
from app.db import engine
from app.scheduler import create_scheduler, tick
from app.services.errors import (
    AlreadyInQueue,
    AuthFailed,
    DomainError,
    InvalidDuration,
    NotAdmin,
    NotInQueue,
    OfferNotActive,
    PinTaken,
    PrinterNotAvailable,
    PrinterReserved,
    TooManyAttempts,
    UserBusy,
)

# Без этого сообщения уровня INFO из наших модулей не видны нигде: uvicorn
# настраивает только свои логгеры, а обработчик по умолчанию пропускает лишь
# WARNING и выше. В контейнере это означало бы, что сверка работает молча.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

# Доменные ошибки несут текст для человека, а код статуса задаётся здесь, чтобы
# сервисы ничего не знали про HTTP.
STATUS_BY_ERROR: dict[type[DomainError], int] = {
    AuthFailed: 401,
    NotAdmin: 403,
    TooManyAttempts: 429,
    NotInQueue: 404,
    PrinterNotAvailable: 409,
    PrinterReserved: 409,
    UserBusy: 409,
    AlreadyInQueue: 409,
    OfferNotActive: 409,
    PinTaken: 409,
    InvalidDuration: 400,
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Бот живёт в том же процессе: при двух принтерах отдельный воркер дал бы
    только лишний контейнер и лишний способ рассинхронизироваться."""
    bot = None
    polling: asyncio.Task | None = None

    if settings.tg_bot_token:
        bot = build_bot()
        attach_notifier(bot)  # до первой сверки, иначе её сообщения пропадут
        polling = asyncio.create_task(start_polling(bot))
    else:
        logger.warning("TG_BOT_TOKEN не задан: бот не запущен, уведомления не уходят")

    # Догоняем всё, что произошло, пока приложение лежало: истёкшие печати,
    # просроченные предложения. Отдельного восстановления заданий не нужно —
    # состояние лежит в таблицах.
    await tick()
    scheduler = create_scheduler()
    scheduler.start()

    yield

    scheduler.shutdown(wait=False)
    if polling is not None:
        polling.cancel()
        with suppress(asyncio.CancelledError):
            await polling
    notify.set_sender(None)
    if bot is not None:
        await bot.session.close()
    await engine.dispose()


app = FastAPI(title=t.API_TITLE, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth_routes.router)
app.include_router(kiosk_routes.router)
app.include_router(admin_routes.router)


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _error_response(request: Request, message: str, code: int) -> Response:
    """На киоске ошибка — это экран с крупным текстом, а не JSON."""
    if _wants_html(request):
        return templates.TemplateResponse(
            request, "error.html", {"message": message}, status_code=code
        )
    return JSONResponse({"error": message}, status_code=code)


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> Response:
    return _error_response(request, str(exc), STATUS_BY_ERROR.get(type(exc), 400))


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException) -> Response:
    return _error_response(request, str(exc.detail), exc.status_code)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "tz": settings.tz}
