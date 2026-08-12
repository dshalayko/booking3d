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
* ``MACHINE_*`` — как называется оборудование каждого типа и что оно делает;
  слова зависят от типа, потому что гравировщик не печатает;
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

Плейсхолдеры именованные (``{machine}``) и подставляются через ``.format()``:
в другом языке порядок слов другой, и по позиции их пришлось бы угадывать.

Не здесь: логи, вывод ``app/cli.py`` и сообщения о незаполненном ``.env``. Их
читает тот, кто держит сервер, а не тот, кто печатает.
"""

from app.enums import MachineKind, MachineStatus

LANG = "ru"  # атрибут <html lang>

# --- время -------------------------------------------------------------------

TIME_FORMAT = "%H:%M"
DATETIME_FORMAT = "%d.%m %H:%M"

UNIT_MINUTES = "{minutes} мин"
UNIT_HOURS = "{hours} ч"
UNIT_HOURS_MINUTES = "{hours} ч {minutes} мин"


# --- типы оборудования -------------------------------------------------------
#
# Заголовок секции на экране, слово в единственном числе для кнопок и глагол
# для статуса «занята». Новый тип в `MachineKind` обязан появиться во всех трёх
# словарях, иначе на стене окажется английское `engraver`.

MACHINE_KIND_TITLE = {
    MachineKind.PRINTER: "Принтеры",
    MachineKind.ENGRAVER: "Гравировщики",
}

MACHINE_KIND_ONE = {
    MachineKind.PRINTER: "принтер",
    MachineKind.ENGRAVER: "гравировщик",
}

# Слово для статуса «занята работой». Остальные статусы от типа не зависят.
MACHINE_BUSY_WORD = {
    MachineKind.PRINTER: "печатает",
    MachineKind.ENGRAVER: "гравирует",
}


# --- бот: справка и команды --------------------------------------------------

BOT_HELP = (
    "Что я умею:\n"
    "/status — что с оборудованием прямо сейчас\n"
    "/my — моя работа и место в очереди\n"
    "/queue — встать в очередь\n"
    "/leave — выйти из очереди\n"
    "/free — освободить машину, которая занята мной\n"
    "/pin — новый PIN для планшета в мастерской\n\n"
    "Занимать машины и вставать в очередь можно с планшета в мастерской, по PIN."
)

# Подписи команд в меню Telegram.
BOT_COMMAND_DESCRIPTIONS = {
    "status": "что с оборудованием",
    "my": "моя работа и очередь",
    "queue": "встать в очередь",
    "queue_printer": "очередь на принтер",
    "queue_engraver": "очередь на гравировщик",
    "leave": "выйти из очереди",
    "free": "освободить машину",
    "pin": "новый PIN",
    "help": "что я умею",
}


# --- бот: регистрация --------------------------------------------------------

BOT_ASK_LOGIN = (
    "Напишите свой корпоративный логин — тот же, что в почте, "
    "вида <b>n_username</b>.\n"
    "Например: <code>d_shalayko</code>.\n\n"
    "PIN выдам сразу после этого: под логином вас будет видно на планшете "
    "в мастерской, когда вы занимаете машину."
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
    "Он нужен, чтобы занимать машины с планшета в мастерской. "
    "Запишите — второй раз я его не покажу, только выдам новый по /pin.\n\n"
    "{help}"
)

BOT_ALREADY_REGISTERED = (
    "{name}, вы уже зарегистрированы.\n\nЗабыли PIN? Новый — командой /pin.\n\n{help}"
)

BOT_PIN_CHANGED = "Новый PIN: <b>{pin}</b>\nСтарый больше не работает."

BOT_NAME_CHANGED = (
    "Администратор поправил ваш логин: было <b>{previous}</b>, стало <b>{login}</b>.\n"
    "Под ним вас видно на планшете в мастерской. PIN не менялся.\n"
    "Если это ошибка — напишите администратору коворкинга."
)

BOT_NOT_REGISTERED = "Сначала напишите /start — я спрошу корпоративный логин и выдам PIN."


# --- бот: статус -------------------------------------------------------------

BOT_STATUS_MARKS = {
    MachineStatus.FREE: "🟢",
    MachineStatus.PRINTING: "🔴",
    MachineStatus.DONE_WAIT: "🟡",
    MachineStatus.BROKEN: "⚪️",
}

BOT_STATUS_MARK_UNKNOWN = "⚪️"

# Слово для «занята» берётся из MACHINE_BUSY_WORD по типу машины.
BOT_STATUS_WORDS = {
    MachineStatus.FREE: "свободен",
    MachineStatus.DONE_WAIT: "готово, деталь на столе",
    MachineStatus.BROKEN: "в обслуживании",
}

BOT_STATUS_SECTION = "<b>{title}</b>"
BOT_STATUS_LINE = "{mark} <b>{machine}</b> — {word}"
BOT_STATUS_RESERVED = "{mark} <b>{machine}</b> — придержан за {name} до {time}"
BOT_STATUS_BUSY = "    {name}, осталось ~{left}"
BOT_STATUS_DONE = "    {name}, готово {ago} назад"
BOT_STATUS_NOTE = "    {note}"

BOT_STATUS_QUEUE = "Очередь: {people}"
BOT_STATUS_QUEUE_EMPTY = "Очередь пуста"
BOT_STATUS_QUEUE_PERSON = "{position}. {name}"
BOT_STATUS_QUEUE_OFFERED = " (приглашён)"

BOT_STATUS_PARK_EMPTY = "В парке пока нет ни одной машины."


# --- бот: моё состояние ------------------------------------------------------

BOT_MY_BUSY = "За вами <b>{machine}</b>, осталось ~{left}.\nОсвободить — /free"
BOT_MY_OFFERED = (
    "Вам предложен <b>{machine}</b> — подойдите до {time}, "
    "потом предложение уйдёт следующему."
)
BOT_MY_IN_QUEUE = "Вы в очереди на {kind}, номер {position}. Выйти — /leave"
BOT_MY_NOTHING = "Сейчас за вами ничего не числится. Что с оборудованием — /status"


# --- бот: очередь ------------------------------------------------------------

# Очередей столько же, сколько типов: ждущий гравировщик и ждущий принтер стоят
# в разных списках, поэтому команду нужно уточнить.
BOT_QUEUE_PICK = "Чего ждёте?\n{options}"
BOT_QUEUE_PICK_OPTION = "{command} — {title}"

BOT_QUEUE_JOINED = (
    "Вы в очереди на {kind}, номер {position}.\n"
    "Когда машина освободится, я напишу — на подтверждение будет 30 минут."
)
BOT_QUEUE_ALREADY = "Вы уже в очереди, номер {position}. Выйти — /leave"
BOT_QUEUE_LEFT = "Вы вышли из очереди."
BOT_QUEUE_REMOVED = "Вас убрали из очереди. Встать заново — /queue"

BOT_OFFER = (
    "<b>{machine}</b> свободен, и очередь дошла до вас.\n"
    "Займите его до <b>{time}</b> — после этого предложение уйдёт следующему.\n"
    "Занять можно с планшета в мастерской."
)
BOT_OFFER_EXPIRED = (
    "Время на {machine} вышло, предложение ушло следующему в очереди.\n"
    "Встать в очередь заново — /queue"
)
BOT_OFFER_NIGHT_HINT = "Ночью время не идёт — отсчёт продолжится утром."

# Подставляется, если имя машины почему-то не нашлось.
BOT_MACHINE_FALLBACK = "машина"


# --- бот: машины -------------------------------------------------------------

BOT_OCCUPIED = (
    "Вы заняли <b>{machine}</b> примерно на {left} (до {time}).\n"
    "Я напишу за 15 минут до конца."
)
BOT_RELEASED = "{machine} освобождён."

# Без рода: имена настоящие, и «Анна снял» читается как ошибка.
BOT_RELEASED_BY_OTHER = (
    "Работа на <b>{machine}</b> остановлена ({name}) — машина снова свободна.\n"
    "Если деталь ещё нужна, заберите её."
)

BOT_ALMOST_DONE = (
    "<b>{machine}</b>: работа заканчивается примерно через {left}.\n"
    "Подойдите забрать деталь — машину ждут другие."
)
BOT_FINISHED = (
    "<b>{machine}</b>: расчётное время вышло.\n"
    "Проверьте результат и заберите деталь. Когда стол будет пустой, "
    "отметьте на планшете «Я забрал деталь» или напишите /free."
)
BOT_CHECK_MACHINE = (
    "На <b>{machine}</b> работа должна была закончиться ({name}). "
    "Если стол пустой, отметьте это на планшете — машина сразу станет вашей."
)
BOT_UNCLAIMED_OWNER = (
    "<b>{machine}</b> занят вашей деталью уже {ago} после окончания работы. "
    "Заберите её, пожалуйста — машину ждут."
)
BOT_UNCLAIMED_QUEUE = (
    "На <b>{machine}</b> деталь ({name}) не забрали {ago}. "
    "Можно снять её и освободить машину — отметьте на планшете."
)

BOT_CANCELLED_BY_ADMIN = "Вашу работу на <b>{machine}</b> сняли."
BOT_CANCELLED_REASON = "\nПричина: {reason}"
BOT_CANCELLED_TAIL = "\nЕсли деталь ещё нужна, заберите её."

BOT_NOTHING_TO_FREE = "За вами не числится занятая машина. Что с оборудованием — /status"


# --- отказы: машины и очередь -------------------------------------------------

ERR_MACHINE_NOT_FOUND = "Машина не найдена"
ERR_MACHINE_BROKEN = "{machine} в обслуживании"
ERR_MACHINE_BUSY = "{machine} уже занят"
ERR_MACHINE_JUST_TAKEN = "{machine} только что заняли"
ERR_MACHINE_NOT_WORKING = "{machine} сейчас не работает"
ERR_MACHINE_NO_SESSION = "У {machine} нет активной работы"
ERR_MACHINE_NOT_BROKEN = "{machine} не в обслуживании"
ERR_MACHINE_RESERVED = "{machine} зарезервирован за первым в очереди"
ERR_MACHINE_RELEASE_FORBIDDEN = "Снять активную работу может только тот, кто её начал"
ERR_QUEUE_WAIT_YOUR_TURN = "Есть очередь — дождитесь своего предложения"

ERR_DURATION = "Длительность должна быть от {min_minutes} минут до {max_hours} часов"

ERR_USER_BUSY = "У вас уже занята машина"
ERR_USER_BUSY_FREE_FIRST = "У вас уже занята машина — сначала освободите её"
ERR_ALREADY_IN_QUEUE = "Вы уже в очереди"
ERR_NOT_IN_QUEUE = "Вас нет в очереди"
ERR_OFFER_NOT_ACTIVE = "Предложение уже неактуально"
ERR_OFFER_WINDOW_OPEN = "Окно ещё не истекло"

# Причина снятия, которая пишется в журнал, когда админ не указал свою.
REASON_MACHINE_BROKEN = "машина выведена в обслуживание"


# --- отказы: состав парка ----------------------------------------------------

ERR_MACHINE_NAME_EMPTY = "Впишите имя машины"
ERR_MACHINE_NAME_LONG = "Имя длиннее {limit} символов не поместится"
ERR_MACHINE_NAME_TAKEN = "Имя {name} уже занято другой машиной"
ERR_MACHINE_KIND_UNKNOWN = "Неизвестный тип оборудования: {kind}"
ERR_MACHINE_HAS_HISTORY = (
    "{machine} не удалить: за ним {sessions} работ(ы) и {offers} приглашени(й) "
    "в журнале. Если машина уехала — выведите её в обслуживание, тогда история "
    "останется целой."
)


# --- отказы: доступ ----------------------------------------------------------

ERR_KIOSK_ONLY = "Занимать машины можно только с планшета в мастерской"
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
ERR_LOGIN_FORMAT = "Логин — латиницей вида n_username, например d_shalayko"
ERR_LOGIN_TAKEN = "Логин {login} уже занят другим человеком"


# --- киоск: плашки после действия --------------------------------------------

FLASH_KIOSK = {
    "occupied": "Машина занята. Хорошей работы!",
    "released": "Машина освобождена",
    "queued": "Вы в очереди — уведомление придёт в Telegram",
    "left": "Вы вышли из очереди",
}

FLASH_ADMIN = {
    "broken": "Машина выведена в обслуживание",
    "fixed": "Машина вернулась в строй",
    "cancelled": "Работа снята",
    "removed": "Человек убран из очереди",
    "pin_reset": "Новый PIN отправлен в Telegram",
    "renamed": "Логин изменён",
    "machine_added": "Машина добавлена в парк",
    "machine_renamed": "Машина переименована",
    "machine_removed": "Машина удалена из парка",
}


# --- киоск: длительность работы ----------------------------------------------

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
    "Стол пустой — машина станет свободна, и следующий в очереди получит уведомление."
)
CONFIRM_RELEASE_TITLE = "Освободить машину?"
CONFIRM_RELEASE_HINT = "Работа будет прервана и отмечена как снятая."
CONFIRM_RELEASE_SUBMIT = "Да, освободить"

CONFIRM_QUEUE_JOIN_TITLE = "Встать в очередь?"
CONFIRM_QUEUE_JOIN_HINT = (
    "Когда машина освободится, уведомление придёт в Telegram "
    "первому в очереди. На подтверждение будет 30 минут."
)
CONFIRM_QUEUE_JOIN_SUBJECT = "Очередь на {title}"
CONFIRM_QUEUE_JOIN_SUBMIT = "Встать в очередь"

CONFIRM_QUEUE_LEAVE_TITLE = "Выйти из очереди?"
CONFIRM_QUEUE_LEAVE_HINT = "Место потеряется, встать снова можно будет в конец."
CONFIRM_QUEUE_LEAVE_SUBJECT = "Очередь"
CONFIRM_QUEUE_LEAVE_SUBMIT = "Выйти"


# --- журнал админки ----------------------------------------------------------
#
# Формулировки без рода: в журнале настоящие имена, и «Анна занял» — это не
# мелочь, а ошибка в тексте, который читают каждый день.

LOG_SESSION_STARTED = "{machine} — занят: {name}"
LOG_SESSION_COMPLETED = "{machine}: деталь забрали"
LOG_SESSION_COMPLETED_BY = " ({name})"
LOG_SESSION_CANCELLED = "{machine}: работа снята"
LOG_SESSION_CANCELLED_BY = ", {name}"
LOG_SESSION_CANCEL_REASON = " — {reason}"

LOG_QUEUE_JOINED = "В очередь на {kind}: {name}"
LOG_QUEUE_OFFERED = "Приглашение на {machine}: {name}"
LOG_QUEUE_RESOLVED = "{word}: {name}"
LOG_QUEUE_TAKEN = "Приглашение принято"
LOG_QUEUE_EXPIRED = "Окно истекло"
LOG_QUEUE_LEFT = "Выход из очереди"


# --- прочее ------------------------------------------------------------------

API_TITLE = "Бронирование оборудования мастерской"


# --- HTML-шаблоны ------------------------------------------------------------
#
# Доступны в Jinja как `t.<ключ>`, регистрация — в app/api/kiosk.py.

UI = {
    # base.html
    "lang": LANG,
    "app_title": "Мастерская",
    "app_short_title": "Мастерская",
    "offline_banner": "Нет связи с сервером. Данные на экране могли устареть.",
    # _board.html
    "board_occupy_cta": "Занять {kind}",
    "board_free_of": "свободен {free} из {total}",
    "board_queue_cta": "Встать в очередь",
    "board_all_busy": "все заняты",
    "board_park_empty": "Парк пуст — заведите машины в админке",
    "tile_broken": "В обслуживании",
    "tile_busy": "Занят",
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
    "occupy_title": "Занять {machine}",
    "occupy_heading": "{machine} — занять сейчас",
    "occupy_duration_label": "Сколько работать",
    "occupy_hint": (
        "Точность неважна — по истечении времени машина не освободится сама, "
        "а попросит проверить результат."
    ),
    "occupy_submit": "Занять сейчас",
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
    "admin_to_board": "На экран мастерской",
    "admin_tab_summary": "Сводка",
    "admin_tab_machines": "Оборудование",
    "admin_machines": "Оборудование",
    "admin_owner_until": "{name}, до {time}",
    "admin_reserved": "придержан за {name} до {time}",
    "admin_fix": "Вернуть в строй",
    "admin_reason_placeholder": "причина снятия",
    "admin_cancel_work": "Снять работу",
    "admin_note_placeholder": "что сломалось",
    "admin_break": "В обслуживание",
    "admin_queue": "Очередь",
    "admin_queue_of": "Очередь: {title}",
    "admin_remove": "Убрать",
    "admin_empty": "Пусто",
    "admin_people": "Люди ({count})",
    "admin_role": "админ",
    "admin_tg": "tg {chat_id}",
    "admin_rename": "Переименовать",
    "admin_new_pin": "Новый PIN",
    "admin_events": "Последние события",
    "admin_no_events": "Пока ничего не происходило",
    # admin_machines.html
    "admin_machines_title": "Оборудование",
    "admin_machines_add": "Добавить машину",
    "admin_machines_name_placeholder": "имя, например P2S #3",
    "admin_machines_kind_label": "Тип",
    "admin_machines_add_submit": "Добавить",
    "admin_machines_none": "Машин этого типа нет",
    "admin_machines_delete": "Удалить",
    "admin_machines_history": "в журнале: работ {sessions}, приглашений {offers}",
    "admin_machines_no_history": "истории нет — можно удалить",
    "admin_machines_hint": (
        "Имя видно на экране в мастерской и в каждом сообщении бота. "
        "Тип после создания не меняется: на машину уже ссылается журнал. "
        "Уехавшую машину с историей не удаляйте, а выведите в обслуживание "
        "на вкладке «Сводка» — она останется в журнале, но занять её будет нельзя."
    ),
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
    "eta_over": "время вышло, проверьте результат",
    "done_ago": "готово {ago} назад",
}
