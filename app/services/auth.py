"""Авторизация.

Бронировать можно только с планшета в мастерской, по PIN. Планшет один раз
регистрируется через `/kiosk/enroll` и получает подписанную device-cookie на 10
лет; без неё форма ввода PIN не отдаётся вовсе (правило 11 из PLAN.md).

Это не украшение, а единственное, что защищает PIN: сервер публичный, а четыре
цифры перебираются из интернета за минуты. С телефона можно смотреть статусы —
доска открыта всем, — но занимать машины и вставать в очередь только у самих
машин.

Сессии у киоска нет вовсе: PIN вводится под каждое действие. Планшет общий, и
любой перенос входа между действиями означал бы, что следующий человек либо
ждёт, пока чужая сессия истечёт, либо занимает машину от чужого имени.

Осталась одна долгоживущая cookie — админская, тоже подписанный токен
(itsdangerous), без строки в БД. Цена решения: отозвать одно устройство нельзя,
только ротацией секрета сразу для всех. При одном-двух планшетах это дешевле
отдельной таблицы.
"""

import secrets as pysecrets
import time
from dataclasses import dataclass, field

from itsdangerous import BadSignature, SignatureExpired, URLSafeSerializer, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.config import settings
from app.models import User
from app.services.errors import AuthFailed, PinTaken, TooManyAttempts
from app.services.security import generate_pin, is_valid_pin_format, pin_digest

DEVICE_COOKIE = "kiosk_device"
ADMIN_COOKIE = "admin"

DEVICE_MAX_AGE = 10 * 365 * 24 * 3600  # киоск живёт до ротации KIOSK_SECRET
ADMIN_SESSION_TTL = 8 * 3600

PIN_MAX_ATTEMPTS = 5
PIN_LOCKOUT_SECONDS = 60


# --- ограничение попыток -----------------------------------------------------


@dataclass
class AttemptLimiter:
    """Пауза после серии неудач.

    Состояние в памяти процесса: приложение однопроцессное, а после рестарта
    сброс счётчика не страшнее, чем сам рестарт.
    """

    max_attempts: int = PIN_MAX_ATTEMPTS
    lockout_seconds: int = PIN_LOCKOUT_SECONDS
    _failures: dict[str, int] = field(default_factory=dict)
    _locked_until: dict[str, float] = field(default_factory=dict)

    def ensure_allowed(self, key: str, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        until = self._locked_until.get(key)
        if until is not None and until > now:
            left = int(until - now) + 1
            raise TooManyAttempts(t.ERR_TOO_MANY_ATTEMPTS.format(seconds=left))

    def register_failure(self, key: str, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        self._prune(now)
        failures = self._failures.get(key, 0) + 1
        self._failures[key] = failures
        if failures >= self.max_attempts:
            self._locked_until[key] = now + self.lockout_seconds
            self._failures[key] = 0

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)

    def _prune(self, now: float) -> None:
        if len(self._locked_until) > 1000:
            self._locked_until = {k: v for k, v in self._locked_until.items() if v > now}
            self._failures.clear()


pin_limiter = AttemptLimiter()


# --- подписанные токены ------------------------------------------------------


def _timed(secret: str, salt: str, name: str) -> URLSafeTimedSerializer:
    if not secret:
        raise RuntimeError(f"{name} не задан — заполни .env")
    return URLSafeTimedSerializer(secret, salt=salt)


def issue_device_token() -> str:
    if not settings.kiosk_secret:
        raise RuntimeError("KIOSK_SECRET не задан — заполни .env")
    return URLSafeSerializer(settings.kiosk_secret, salt="kiosk-device").dumps({"kiosk": True})


def is_kiosk_device(token: str | None) -> bool:
    if not token or not settings.kiosk_secret:
        return False
    try:
        payload = URLSafeSerializer(settings.kiosk_secret, salt="kiosk-device").loads(token)
    except BadSignature:
        return False
    return bool(payload.get("kiosk"))


def issue_admin_session() -> str:
    return _timed(settings.session_secret, "admin-session", "SESSION_SECRET").dumps("admin")


def is_admin_session(token: str | None) -> bool:
    if not token or not settings.session_secret:
        return False
    try:
        _timed(settings.session_secret, "admin-session", "SESSION_SECRET").loads(
            token, max_age=ADMIN_SESSION_TTL
        )
    except (BadSignature, SignatureExpired):
        return False
    return True


# --- секреты из окружения ----------------------------------------------------


def _secret_matches(value: str | None, expected: str) -> bool:
    """Сравнение за постоянное время.

    Сравниваем байты, а не строки: `compare_digest` на строках с не-ASCII
    бросает TypeError, и присланная кириллица превратилась бы в 500 вместо
    честного отказа.
    """
    if not value or not expected:
        return False
    return pysecrets.compare_digest(value.encode(), expected.encode())


def verify_enroll_secret(value: str | None) -> bool:
    return _secret_matches(value, settings.kiosk_enroll_secret)


def verify_admin_secret(value: str | None) -> bool:
    return _secret_matches(value, settings.admin_secret)


# --- PIN ---------------------------------------------------------------------


async def user_by_pin(db: AsyncSession, pin: str) -> User:
    """Найти человека по PIN. Формат и наличие — одна и та же ошибка наружу."""
    if not is_valid_pin_format(pin):
        raise AuthFailed(t.ERR_PIN_FORMAT)
    user = await db.scalar(select(User).where(User.pin_digest == pin_digest(pin)))
    if user is None:
        raise AuthFailed(t.ERR_PIN_WRONG)
    return user


async def pick_free_pin(db: AsyncSession, attempts: int = 20) -> str:
    """Свободный PIN. Занятых мало по сравнению с 10 000 вариантов."""
    for _ in range(attempts):
        pin = generate_pin()
        taken = await db.scalar(select(User.id).where(User.pin_digest == pin_digest(pin)))
        if taken is None:
            return pin
    raise PinTaken(t.ERR_PIN_NOT_PICKED)


async def assign_pin(db: AsyncSession, user: User) -> str:
    """Выдать существующему человеку новый PIN."""
    pin = await pick_free_pin(db)
    user.pin_digest = pin_digest(pin)
    await db.flush()
    return pin


async def set_pin(db: AsyncSession, user: User, pin: str) -> None:
    if not is_valid_pin_format(pin):
        raise AuthFailed(t.ERR_PIN_FORMAT)
    digest = pin_digest(pin)
    taken = await db.scalar(select(User.id).where(User.pin_digest == digest))
    if taken is not None and taken != user.id:
        raise PinTaken(t.ERR_PIN_TAKEN)
    user.pin_digest = digest
    await db.flush()
