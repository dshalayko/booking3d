"""Маршруты входа.

Киоск: `/kiosk/enroll` один раз ставит device-cookie — и это весь вход. Входа
по PIN как отдельного шага нет: PIN вводится под каждое действие, см.
api/kiosk.py.

Админ: `/admin/login` по `ADMIN_SECRET`. Доступен из интернета, поэтому секрет —
единственное, что отделяет постороннего от снятия чужой печати: 256 бит, приём
только POST-формой (в URL он попал бы в логи и историю браузера) и пауза после
пяти неудач тем же лимитером, что у PIN. См. DEPLOY.md.

Смотреть статусы можно откуда угодно и без входа.
"""

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse

from app import texts as t
from app.api.deps import client_key, cookie_params
from app.services import auth

router = APIRouter()


@router.get("/kiosk/enroll")
async def enroll_kiosk(request: Request, secret: str = "") -> RedirectResponse:
    """Разовая регистрация планшета как киоска.

    Секрет передаётся в URL: действие выполняется один раз руками при настройке
    iPad, ссылку после этого можно забыть. Держать её в закладках не нужно.
    """
    if not auth.verify_enroll_secret(secret):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t.ERR_BAD_ENROLL_SECRET)

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        auth.DEVICE_COOKIE, auth.issue_device_token(), **cookie_params(auth.DEVICE_MAX_AGE)
    )
    return response


@router.get("/logout")
async def logout() -> RedirectResponse:
    """Выйти из админки. Device-cookie не трогаем — планшет остаётся киоском."""
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(auth.ADMIN_COOKIE, path="/")
    return response


@router.post("/admin/login")
async def admin_login(request: Request, secret: str = Form(...)) -> RedirectResponse:
    key = client_key(request)
    auth.pin_limiter.ensure_allowed(key)

    if not auth.verify_admin_secret(secret):
        auth.pin_limiter.register_failure(key)
        raise HTTPException(status.HTTP_403_FORBIDDEN, t.ERR_BAD_ADMIN_SECRET)

    auth.pin_limiter.reset(key)
    response = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        auth.ADMIN_COOKIE, auth.issue_admin_session(), **cookie_params(auth.ADMIN_SESSION_TTL)
    )
    return response


@router.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots() -> str:
    """Статусы принтеров и имена людей не должны попадать в поисковики."""
    return "User-agent: *\nDisallow: /\n"
