"""PIN: генерация и превращение в то, что хранится в БД.

Почему HMAC, а не bcrypt.

PIN — четыре цифры, всего 10 000 вариантов, и на киоске человек вводит только
его, без имени. Значит по PIN нужно **находить** пользователя, а перебирать
bcrypt-хеши всех членов коворкинга на каждый ввод — это секунды на запрос.

HMAC с серверным «перцем» (`PIN_PEPPER` из окружения) решает обе задачи:
детерминированное значение можно проиндексировать уникальным индексом, а по
одной только украденной базе PIN не восстановить — перец лежит в переменных
окружения, а не в дампе. Против перебора работают не медленные хеши, а то, что
форма ввода доступна только на устройстве-киоске, и ограничение попыток.

Ротация `PIN_PEPPER` обнуляет все PIN-ы разом — их придётся выдать заново.
"""

import hashlib
import hmac
import secrets

from app.config import settings

PIN_LENGTH = 4


def generate_pin() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(PIN_LENGTH))


def pin_digest(pin: str) -> str:
    """Детерминированный отпечаток PIN для поиска и сравнения."""
    if not settings.pin_pepper:
        raise RuntimeError("PIN_PEPPER не задан — заполни .env")
    return hmac.new(
        settings.pin_pepper.encode(), pin.strip().encode(), hashlib.sha256
    ).hexdigest()


def is_valid_pin_format(pin: str) -> bool:
    pin = pin.strip()
    return len(pin) == PIN_LENGTH and pin.isdigit()
