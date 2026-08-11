"""Выбор языка интерфейса.

Тексты лежат по файлу на язык: `ru.py`, `en.py`. Здесь только выбор — весь
остальной код импортирует `app.texts` и про язык не знает.

Язык задаётся переменной `UI_LANG` в .env. Не `LANG`: так называется системная
переменная локали, и pydantic-settings подхватил бы из окружения что-нибудь
вроде `en_US.UTF-8`.

Незнакомый язык роняет приложение на старте, а не откатывается к русскому.
Опечатка в `UI_LANG` иначе означала бы русский экран на стене англоязычного
офиса — молча и до тех пор, пока кто-нибудь не пожалуется.

Новый язык: скопировать `ru.py`, перевести значения, оставив имена и
плейсхолдеры, и добавить сюда в `LANGUAGES`. Совпадение набора имён проверяет
`tests/test_texts.py`.
"""

from app.config import settings

LANGUAGES = ("ru", "en")

if settings.ui_lang not in LANGUAGES:
    raise RuntimeError(
        f"UI_LANG={settings.ui_lang!r} — нет такого языка. Есть: {', '.join(LANGUAGES)}"
    )

if settings.ui_lang == "en":
    from app.texts.en import *  # noqa: F403
else:
    from app.texts.ru import *  # noqa: F403
