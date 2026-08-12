"""Шаблоны: один набор на киоск и на Mini App.

Экраны у планшета на стене и у телефона в Telegram одни и те же — расписание,
бронь, подтверждения. Отличий ровно два, и оба приезжают в контекст:

* ``base`` — префикс адресов действий: пусто у киоска, ``/app`` у Mini App;
* ``needs_pin`` — рисовать ли клавиатуру PIN. На киоске PIN подписывает каждое
  действие (правило 11), в Mini App личность приходит подписанной от Telegram.

Значения по умолчанию — киосковые: он появился первым, и его шаблоны не должны
обрастать проверками ради второго клиента.

Модуль отдельный, а не внутри api/kiosk.py, потому что иначе Mini App
импортировал бы киоск ради фильтров дат, а обработчик ошибок в main.py — ради
`error.html`.
"""

from datetime import datetime

from fastapi.templating import Jinja2Templates

from app import texts as t
from app.config import settings
from app.enums import MachineKind

templates = Jinja2Templates(directory="app/templates")


def hhmm(value: datetime | None) -> str:
    return value.astimezone(settings.zone).strftime(t.TIME_FORMAT) if value else ""


def when(value: datetime | None) -> str:
    """Дата и время: у брони на будущее «в 14:00» без числа ничего не значит."""
    return value.astimezone(settings.zone).strftime(t.DATETIME_FORMAT) if value else ""


def iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def day(value: datetime | None) -> str:
    """Местная дата в виде YYYY-MM-DD — так её ждёт адрес расписания."""
    return value.astimezone(settings.zone).date().isoformat() if value else ""


# Те же функции обработчикам нужны и без шаблона — в текстах отказов.
templates.env.filters["hhmm"] = hhmm
templates.env.filters["when"] = when
templates.env.filters["iso"] = iso
templates.env.filters["day"] = day

# Надписи шаблонов доступны как `t.<ключ>`, строки для браузера — как `t_js`.
# Глобальные, а не в контексте: их ждёт base.html, который рисуется на каждый
# ответ, включая экран ошибки из main.py.
templates.env.globals["t"] = t.UI
templates.env.globals["t_js"] = t.JS
# Названия типов оборудования: шаблонам они нужны и на доске, и в админке, а
# передавать один и тот же словарь в каждый контекст — лишний повод забыть.
templates.env.globals["MACHINE_KIND_TITLE"] = t.MACHINE_KIND_TITLE
templates.env.globals["MACHINE_KIND_ONE"] = t.MACHINE_KIND_ONE
templates.env.globals["MACHINE_KINDS"] = tuple(MachineKind)
# Клиент по умолчанию — киоск; Mini App переопределяет эти значения в контексте
# (см. api/screens.py, `Client`). Глобальные значения нужны экранам, которые
# рисуются вне обоих клиентов: ошибка из main.py, офлайн-заглушка, админка.
templates.env.globals["base"] = ""
templates.env.globals["needs_pin"] = True
templates.env.globals["telegram_sdk"] = False
