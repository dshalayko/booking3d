"""Подпись Telegram Mini App.

Mini App — это наша же страница, открытая во встроенном браузере Telegram. При
открытии Telegram отдаёт странице `initData`: query-строку с полями `user`,
`auth_date`, `query_id` и подписью `hash`, посчитанной на токене нашего бота.
Проверив подпись, сервер узнаёт, кто пришёл, — без пароля, без PIN и без
одноразовых ссылок. Описание формата:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Это и есть причина, по которой правило 11 (действия только с планшета по PIN)
удалось смягчить, не открывая PIN интернету: с телефона действия подписывает
Telegram, а четыре цифры за пределами мастерской не вводятся вообще.

Ключ и данные в HMAC идут в порядке, который легко перепутать: ключ — строка
`"WebAppData"`, данные — токен бота. Перепутанные местами дают стабильно неверный
хеш, и отладка этого места занимает вечер, поэтому здесь оно с комментарием.
"""

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from app import texts as t
from app.config import settings
from app.services.errors import BadInitData

logger = logging.getLogger(__name__)

# Сколько живёт подпись открытия. Telegram ставит `auth_date` в момент, когда
# страница открылась; сутки — запас на то, что человек свернул приложение и
# вернулся к нему вечером. Дальше Mini App переоткрывается и подписывается заново.
INIT_DATA_MAX_AGE = 24 * 3600


def check_init_data(init_data: str, now: float | None = None) -> int:
    """Вернуть Telegram-id открывшего Mini App или отказать.

    Отказ один на все причины — подделанная подпись, просроченное открытие,
    отсутствующий `user`: наружу разница не нужна, а в логе она есть.
    """
    now = now if now is not None else time.time()

    if not settings.tg_bot_token:
        # Без токена подпись проверять нечем: считаем, что не сходится.
        logger.warning("Mini App: TG_BOT_TOKEN не задан, подпись проверить нечем")
        raise BadInitData(t.ERR_APP_BAD_INIT_DATA)

    fields = dict(parse_qsl(init_data or "", keep_blank_values=True))
    received = fields.pop("hash", "")
    if not received:
        raise BadInitData(t.ERR_APP_BAD_INIT_DATA)

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", settings.tg_bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        logger.info("Mini App: подпись initData не сошлась")
        raise BadInitData(t.ERR_APP_BAD_INIT_DATA)

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError as exc:
        raise BadInitData(t.ERR_APP_BAD_INIT_DATA) from exc
    if auth_date <= 0 or now - auth_date > INIT_DATA_MAX_AGE:
        logger.info("Mini App: initData просрочен (auth_date=%s)", auth_date)
        raise BadInitData(t.ERR_APP_BAD_INIT_DATA)

    try:
        chat_id = int(json.loads(fields.get("user") or "{}")["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise BadInitData(t.ERR_APP_BAD_INIT_DATA) from exc
    return chat_id
