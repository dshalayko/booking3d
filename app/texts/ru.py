"""Русские тексты интерфейса.

Здесь и только здесь лежат формулировки: сообщения бота, надписи на киоске,
тексты отказов и строки журнала админки. Код их не сочиняет, а берёт отсюда по
имени — поэтому поправить фразу можно, не открывая логику.

Файл-близнец — `en.py`. Какой из них соберётся, решает `app/texts/__init__.py`
по переменной `UI_LANG`; код везде импортирует `app.texts` и про язык не знает.
Набор имён в файлах языков обязан совпадать — это проверяет
`tests/test_texts.py`.

Что где:

* ``UNIT_*``, ``*_FORMAT`` — единицы времени и формат часов, они тоже меняются
  при переводе;
* ``BOT_*`` — сообщения Telegram-бота; собирают их функции в ``app/bot/texts.py``,
  своих формулировок у них нет;
* ``ERR_*`` — доменные ошибки и HTTP-отказы; этот же текст попадает на экран
  ошибки киоска, поэтому он объясняет, что делать, а не что сломалось;
* ``FLASH_*`` — зелёная плашка после успешного действия;
* ``CONFIRM_*``, ``DURATION_*`` — экраны подтверждения и выбора длительности;
* ``LOG_*`` — журнал событий в админке;
* ``UI`` — надписи в HTML-шаблонах, доступны в Jinja как ``t.<ключ>``;
* ``JS`` — строки, которые дорисовывает браузер (счётчики времени), уезжают в
  страницу из ``base.html`` в ``window.T``.

Плейсхолдеры именованные (``{printer}``) и подставляются через ``.format()``:
в другом языке порядок слов другой, и по позиции их пришлось бы угадывать.

Не здесь: логи, вывод ``app/cli.py`` и сообщения о незаполненном ``.env``. Их
читает тот, кто держит сервер, а не тот, кто печатает.
"""

from app.enums import PrinterStatus

LANG = "ru"  # атрибут <html lang>

# --- время -------------------------------------------------------------------

TIME_FORMAT = "%H:%M"
DATETIME_FORMAT = "%d.%m %H:%M"

UNIT_MINUTES = "{minutes} мин"
UNIT_HOURS = "{hours} ч"
UNIT_HOURS_MINUTES = "{hours} ч {minutes} мин"


# --- бот: справка и команды --------------------------------------------------

BOT_HELP = (
    "Что я умею:\n"
    "/status — что с принтерами прямо сейчас\n"
    "/my — моя печать и место в очереди\n"
    "/queue — встать в очередь\n"
    "/leave — выйти из очереди\n"
    "/free — освободить принтер, который занят мной\n"
    "/pin — новый PIN для планшета у принтеров\n\n"
    "Занимать принтеры и вставать в очередь можно с планшета у принтеров, по PIN."
)

# Подписи команд в меню Telegram.
BOT_COMMAND_DESCRIPTIONS = {
    "status": "что с принтерами",
    "my": "моя печать и очередь",
    "queue": "встать в очередь",
    "leave": "выйти из очереди",
    "free": "освободить принтер",
    "pin": "новый PIN",
    "help": "что я умею",
}


# --- бот: регистрация --------------------------------------------------------

BOT_ASK_LOGIN = (
    "Напишите свой корпоративный логин — тот же, что в почте, "
    "вида <b>n_username</b>.\n"
    "Например: <code>d_shalayko</code>.\n\n"
    "PIN выдам сразу после этого: под логином вас будет видно на планшете "
    "у принтеров, когда вы их занимаете."
)

BOT_BAD_LOGIN = (
    "Это не похоже на корпоративный логин.\n"
    "Нужен вид <b>n_username</b> латиницей: буква, подчёркивание, фамилия. "
    "Например: <code>d_shalayko</code>."
)

BOT_LOGIN_TAKEN = (
    "Логин <b>{login}</b> уже занят другим аккаунтом Telegram.\n"
    "Если это ваш логин — напишите администратору коворкинга."
)

BOT_WELCOME = (
    "Готово, вы зарегистрированы как <b>{login}</b>.\n\n"
    "Ваш PIN: <b>{pin}</b>\n"
    "Он нужен, чтобы занимать принтеры с планшета на стене. "
    "Запишите — второй раз я его не покажу, только выдам новый по /pin.\n\n"
    "{help}"
)

BOT_ALREADY_REGISTERED = (
    "{name}, вы уже зарегистрированы.\n\nЗабыли PIN? Новый — командой /pin.\n\n{help}"
)

BOT_PIN_CHANGED = "Новый PIN: <b>{pin}</b>\nСтарый больше не работает."

BOT_NOT_REGISTERED = "Сначала напишите /start — я спрошу корпоративный логин и выдам PIN."


# --- бот: статус -------------------------------------------------------------

BOT_STATUS_MARKS = {
    PrinterStatus.FREE: "🟢",
    PrinterStatus.PRINTING: "🔴",
    PrinterStatus.DONE_WAIT: "🟡",
    PrinterStatus.BROKEN: "⚪️",
}

BOT_STATUS_MARK_UNKNOWN = "⚪️"

BOT_STATUS_WORDS = {
    PrinterStatus.FREE: "свободен",
    PrinterStatus.PRINTING: "печатает",
    PrinterStatus.DONE_WAIT: "готово, деталь на столе",
    PrinterStatus.BROKEN: "в обслуживании",
}

BOT_STATUS_LINE = "{mark} <b>{printer}</b> — {word}"
BOT_STATUS_RESERVED = "{mark} <b>{printer}</b> — придержан за {name} до {time}"
BOT_STATUS_PRINTING = "    {name}, осталось ~{left}"
BOT_STATUS_DONE = "    {name}, готово {ago} назад"
BOT_STATUS_NOTE = "    {note}"

BOT_STATUS_QUEUE = "Очередь: {people}"
BOT_STATUS_QUEUE_EMPTY = "Очередь пуста"
BOT_STATUS_QUEUE_PERSON = "{position}. {name}"
BOT_STATUS_QUEUE_OFFERED = " (приглашён)"


# --- бот: моё состояние ------------------------------------------------------

BOT_MY_PRINTING = "Вы печатаете на <b>{printer}</b>, осталось ~{left}.\nОсвободить — /free"
BOT_MY_OFFERED = (
    "Вам предложен <b>{printer}</b> — подойдите до {time}, "
    "потом предложение уйдёт следующему."
)
BOT_MY_IN_QUEUE = "Вы в очереди, номер {position}. Выйти — /leave"
BOT_MY_NOTHING = "Сейчас за вами ничего не числится. Что с принтерами — /status"


# --- бот: очередь ------------------------------------------------------------

BOT_QUEUE_JOINED = (
    "Вы в очереди, номер {position}.\n"
    "Когда принтер освободится, я напишу — на подтверждение будет 30 минут."
)
BOT_QUEUE_ALREADY = "Вы уже в очереди, номер {position}. Выйти — /leave"
BOT_QUEUE_LEFT = "Вы вышли из очереди."
BOT_QUEUE_REMOVED = "Вас убрали из очереди. Встать заново — /queue"

BOT_OFFER = (
    "<b>{printer}</b> свободен, и очередь дошла до вас.\n"
    "Займите его до <b>{time}</b> — после этого предложение уйдёт следующему.\n"
    "Занять можно с планшета у принтеров."
)
BOT_OFFER_EXPIRED = (
    "Время на {printer} вышло, предложение ушло следующему в очереди.\n"
    "Встать в очередь заново — /queue"
)
BOT_OFFER_NIGHT_HINT = "Ночью время не идёт — отсчёт продолжится утром."

# Подставляется, если имя принтера почему-то не нашлось.
BOT_PRINTER_FALLBACK = "принтер"


# --- бот: принтеры -----------------------------------------------------------

BOT_OCCUPIED = (
    "Вы заняли <b>{printer}</b> примерно на {left} (до {time}).\n"
    "Я напишу за 15 минут до конца."
)
BOT_RELEASED = "{printer} освобождён."

# Без рода: имена настоящие, и «Анна снял» читается как ошибка.
BOT_RELEASED_BY_OTHER = (
    "Печать на <b>{printer}</b> остановлена ({name}) — принтер снова свободен.\n"
    "Если деталь ещё нужна, заберите её у принтера."
)

BOT_ALMOST_DONE = (
    "<b>{printer}</b>: печать заканчивается примерно через {left}.\n"
    "Подойдите забрать деталь — принтер ждут другие."
)
BOT_FINISHED = (
    "<b>{printer}</b>: расчётное время печати вышло.\n"
    "Проверьте печать и заберите деталь. Когда стол будет пустой, "
    "отметьте на планшете «Я забрал деталь» или напишите /free."
)
BOT_CHECK_PRINTER = (
    "На <b>{printer}</b> печать должна была закончиться ({name}). "
    "Если стол пустой, отметьте это на планшете — принтер сразу станет вашим."
)
BOT_UNCLAIMED_OWNER = (
    "<b>{printer}</b> занят вашей деталью уже {ago} после окончания печати. "
    "Заберите её, пожалуйста — принтер ждут."
)
BOT_UNCLAIMED_QUEUE = (
    "На <b>{printer}</b> деталь ({name}) не забрали {ago}. "
    "Можно снять её и освободить принтер — отметьте на планшете."
)

BOT_CANCELLED_BY_ADMIN = "Вашу печать на <b>{printer}</b> сняли."
BOT_CANCELLED_REASON = "\nПричина: {reason}"
BOT_CANCELLED_TAIL = "\nЕсли деталь ещё нужна, заберите её у принтера."

BOT_NOTHING_TO_FREE = "За вами не числится занятый принтер. Что с принтерами — /status"


# --- отказы: принтеры и очередь ----------------------------------------------

ERR_PRINTER_NOT_FOUND = "Принтер не найден"
ERR_PRINTER_BROKEN = "{printer} в обслуживании"
ERR_PRINTER_BUSY = "{printer} уже занят"
ERR_PRINTER_JUST_TAKEN = "{printer} только что заняли"
ERR_PRINTER_NOT_PRINTING = "{printer} не печатает"
ERR_PRINTER_NO_SESSION = "У {printer} нет активной печати"
ERR_PRINTER_NOT_BROKEN = "{printer} не в обслуживании"
ERR_PRINTER_RESERVED = "{printer} зарезервирован за первым в очереди"
ERR_QUEUE_WAIT_YOUR_TURN = "Есть очередь — дождитесь своего предложения"

ERR_DURATION = "Длительность должна быть от {min_minutes} минут до {max_hours} часов"

ERR_USER_BUSY = "У вас уже занят принтер"
ERR_USER_BUSY_FREE_FIRST = "У вас уже занят принтер — сначала освободите его"
ERR_ALREADY_IN_QUEUE = "Вы уже в очереди"
ERR_NOT_IN_QUEUE = "Вас нет в очереди"
ERR_OFFER_NOT_ACTIVE = "Предложение уже неактуально"
ERR_OFFER_WINDOW_OPEN = "Окно ещё не истекло"

# Причина снятия, которая пишется в журнал, когда админ не указал свою.
REASON_PRINTER_BROKEN = "принтер выведен в обслуживание"


# --- отказы: доступ ----------------------------------------------------------

ERR_KIOSK_ONLY = "Занимать принтеры можно только с планшета у принтеров"
ERR_ADMIN_ONLY = "Действие доступно только админу"
ERR_ADMIN_LOGIN_REQUIRED = "Нужен вход администратора"
ERR_BAD_ENROLL_SECRET = "Неверный секрет регистрации"
ERR_BAD_ADMIN_SECRET = "Неверный секрет"

ERR_PIN_FORMAT = "PIN — четыре цифры"
ERR_PIN_WRONG = "Неверный PIN"
ERR_PIN_TAKEN = "Такой PIN уже занят, выберите другой"
ERR_PIN_NOT_PICKED = "Не удалось подобрать свободный PIN, попробуйте ещё раз"
ERR_TOO_MANY_ATTEMPTS = "Слишком много попыток, подождите {seconds} с"

ERR_NO_ADMIN_IN_DB = "В базе нет ни одного админа. Заведите: python -m app.cli make_admin <tg_id>"
ERR_REASON_REQUIRED = "Укажите причину"
ERR_USER_NOT_FOUND = "Человек не найден"


# --- киоск: плашки после действия --------------------------------------------

FLASH_KIOSK = {
    "occupied": "Принтер занят. Хорошей печати!",
    "released": "Принтер освобождён",
    "queued": "Вы в очереди — уведомление придёт в Telegram",
    "left": "Вы вышли из очереди",
}

FLASH_ADMIN = {
    "broken": "Принтер выведен в обслуживание",
    "fixed": "Принтер вернулся в строй",
    "cancelled": "Печать снята",
    "removed": "Человек убран из очереди",
    "pin_reset": "Новый PIN отправлен в Telegram",
}


# --- киоск: длительность печати ----------------------------------------------

DURATION_LABELS = {
    60: "1 ч",
    120: "2 ч",
    240: "4 ч",
    480: "8 ч",
    720: "12 ч",
}
DURATION_NIGHT = "до утра"


# --- киоск: экраны подтверждения ---------------------------------------------

CONFIRM_CLAIM_TITLE = "Деталь забрали?"
CONFIRM_CLAIM_HINT = (
    "Стол пустой — принтер станет свободен, и следующий в очереди получит уведомление."
)
CONFIRM_RELEASE_TITLE = "Освободить принтер?"
CONFIRM_RELEASE_HINT = "Печать будет прервана и отмечена как снятая."
CONFIRM_RELEASE_SUBMIT = "Да, освободить"

CONFIRM_QUEUE_JOIN_TITLE = "Встать в очередь?"
CONFIRM_QUEUE_JOIN_HINT = (
    "Когда принтер освободится, уведомление придёт в Telegram "
    "первому в очереди. На подтверждение будет 30 минут."
)
CONFIRM_QUEUE_JOIN_SUBJECT = "Очередь общая на все принтеры"
CONFIRM_QUEUE_JOIN_SUBMIT = "Встать в очередь"

CONFIRM_QUEUE_LEAVE_TITLE = "Выйти из очереди?"
CONFIRM_QUEUE_LEAVE_HINT = "Место потеряется, встать снова можно будет в конец."
CONFIRM_QUEUE_LEAVE_SUBJECT = "Очередь"
CONFIRM_QUEUE_LEAVE_SUBMIT = "Выйти"


# --- журнал админки ----------------------------------------------------------
#
# Формулировки без рода: в журнале настоящие имена, и «Анна занял» — это не
# мелочь, а ошибка в тексте, который читают каждый день.

LOG_SESSION_STARTED = "{printer} — занят: {name}"
LOG_SESSION_COMPLETED = "{printer}: деталь забрали"
LOG_SESSION_COMPLETED_BY = " ({name})"
LOG_SESSION_CANCELLED = "{printer}: печать снята"
LOG_SESSION_CANCELLED_BY = ", {name}"
LOG_SESSION_CANCEL_REASON = " — {reason}"

LOG_QUEUE_JOINED = "В очередь: {name}"
LOG_QUEUE_OFFERED = "Приглашение на {printer}: {name}"
LOG_QUEUE_RESOLVED = "{word}: {name}"
LOG_QUEUE_TAKEN = "Приглашение принято"
LOG_QUEUE_EXPIRED = "Окно истекло"
LOG_QUEUE_LEFT = "Выход из очереди"


# --- прочее ------------------------------------------------------------------

API_TITLE = "Бронирование 3D-принтеров"


# --- HTML-шаблоны ------------------------------------------------------------
#
# Доступны в Jinja как `t.<ключ>`, регистрация — в app/api/kiosk.py.

UI = {
    # base.html
    "lang": LANG,
    "app_title": "Принтеры",
    "app_short_title": "Принтеры",
    "offline_banner": "Нет связи с сервером. Данные на экране могли устареть.",
    # _board.html
    "board_occupy_cta": "Занять принтер",
    "board_free_of": "свободен {free} из {total}",
    "board_queue_cta": "Встать в очередь",
    "board_all_busy": "все принтеры заняты",
    "tile_broken": "В обслуживании",
    "tile_printing": "Печатает",
    "tile_until": "до {time}",
    "tile_release": "Освободить",
    "tile_done_wait": "Заберите деталь",
    "tile_done_at": "готово в {time}",
    "tile_claimed": "Я забрал деталь",
    "tile_reserved": "Зарезервирован",
    "tile_its_me": "Это я",
    "tile_free": "Свободен",
    "tile_occupy": "Занять",
    "queue_offered": "приглашён",
    "queue_empty": "Очередь пуста",
    "queue_join": "Встать в очередь",
    "queue_leave": "Выйти из очереди",
    # _keypad.html
    "keypad_label": "PIN",
    "keypad_clear": "Сброс",
    "keypad_hint": "PIN выдаёт бот по команде /start",
    # confirm.html / occupy.html
    "cancel": "Отмена",
    "occupy_title": "Занять {printer}",
    "occupy_heading": "{printer} — занять сейчас",
    "occupy_duration_label": "Сколько печатать",
    "occupy_hint": (
        "Точность неважна — по истечении времени принтер не освободится сам, "
        "а попросит проверить печать."
    ),
    # error.html / offline.html
    "error_title": "Не получилось",
    "error_ok": "Понятно",
    "offline_title": "Нет связи",
    "offline_line1": "Нет связи с сервером.",
    "offline_line2": (
        "Бронирование сейчас не работает — договоритесь голосом, "
        "а потом отметьте в системе."
    ),
    "offline_retry": "Попробовать снова",
    # admin_login.html
    "admin_login_title": "Вход в админку",
    "admin_login_hint": "Секрет из переменной",
    "admin_login_submit": "Войти",
    # admin.html
    "admin_title": "Админка",
    "admin_to_board": "На экран принтеров",
    "admin_printers": "Принтеры",
    "admin_owner_until": "{name}, до {time}",
    "admin_reserved": "придержан за {name} до {time}",
    "admin_fix": "Вернуть в строй",
    "admin_reason_placeholder": "причина снятия",
    "admin_cancel_print": "Снять печать",
    "admin_note_placeholder": "что сломалось",
    "admin_break": "В обслуживание",
    "admin_queue": "Очередь",
    "admin_remove": "Убрать",
    "admin_empty": "Пусто",
    "admin_people": "Люди ({count})",
    "admin_role": "админ",
    "admin_tg": "tg {chat_id}",
    "admin_new_pin": "Новый PIN",
    "admin_events": "Последние события",
    "admin_no_events": "Пока ничего не происходило",
}


# --- строки для браузера -----------------------------------------------------
#
# Уезжают в страницу как `window.T` (base.html) и используются в app.js, где
# счётчики времени перерисовываются каждые 30 секунд без запроса к серверу.

JS = {
    "unit_minutes": UNIT_MINUTES,
    "unit_hours": UNIT_HOURS,
    "unit_hours_minutes": UNIT_HOURS_MINUTES,
    "eta_left": "осталось ~{left}",
    "eta_over": "время вышло, проверьте печать",
    "done_ago": "готово {ago} назад",
}
