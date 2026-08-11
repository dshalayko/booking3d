"""English interface texts.

Twin of `ru.py`: same names, same placeholders, translated values. Which one
gets loaded is decided by `app/texts/__init__.py` from `UI_LANG`.

**Vocabulary.** Picked once and used everywhere — the same action must not be
called two different things on the wall and in the bot:

* **book** a printer, not "take" or "occupy". The whole thing is a booking
  system, and "book / booked / free up" is how people already talk about
  meeting rooms;
* **line**, not "queue": you stand in it for a physical machine down the
  corridor, and "join the line" is what a person would actually say;
* **part** is the physical thing on the bed, **print** is the job. Makers talk
  that way: "your print is done, take your part off the bed";
* **out of service** for a broken printer — "under maintenance" sounds like
  somebody is scheduled to come and service it;
* **up next** for whoever got the offer, instead of a literal "invited".

Contractions are deliberate ("you're", "I'll", "it's"). These are messages from
a bot to a colleague, not terms of service.

**Not translated:** the commands themselves (`/queue`, `/free`, `/pin`). They
are identifiers wired to handlers in `app/bot/bot.py` and to Telegram's command
list; only their captions change with the language.

Other notes:

* time stays 24-hour and the date is day-first with a spelled-out month
  (`11 Aug 14:00`): the screen hangs on a wall in a mixed office, and `08.11`
  reads as two different days depending on who is looking at it;
* `1 h` / `2 h` rather than `1 hour` / `2 hours`: the counters are assembled
  from parts, so plural forms would need logic, not strings — see `humanize()`
  in `app/bot/texts.py`.
"""

from app.enums import PrinterStatus

LANG = "en"  # <html lang> attribute

# --- time --------------------------------------------------------------------

TIME_FORMAT = "%H:%M"
DATETIME_FORMAT = "%d %b %H:%M"

UNIT_MINUTES = "{minutes} min"
UNIT_HOURS = "{hours} h"
UNIT_HOURS_MINUTES = "{hours} h {minutes} min"


# --- bot: help and commands --------------------------------------------------

BOT_HELP = (
    "Here's what I can do:\n"
    "/status — how the printers are doing right now\n"
    "/my — my print and my place in line\n"
    "/queue — join the line\n"
    "/leave — leave the line\n"
    "/free — free up the printer I booked\n"
    "/pin — a new PIN for the tablet by the printers\n\n"
    "You book printers and join the line from the tablet by the printers, using your PIN."
)

# Command captions in the Telegram menu.
BOT_COMMAND_DESCRIPTIONS = {
    "status": "how the printers are doing",
    "my": "my print and my place in line",
    "queue": "join the line",
    "leave": "leave the line",
    "free": "free up my printer",
    "pin": "get a new PIN",
    "help": "what I can do",
}


# --- bot: registration -------------------------------------------------------

BOT_ASK_LOGIN = (
    "Send me your work login — the same one you use for email, "
    "shaped like <b>n_username</b>.\n"
    "For example: <code>d_shalayko</code>.\n\n"
    "Your PIN comes right after. Your login is what everyone sees on the tablet "
    "by the printers when you book one."
)

BOT_BAD_LOGIN = (
    "That doesn't look like a work login.\n"
    "It should look like <b>n_username</b> in Latin letters: a letter, an underscore, "
    "then your surname. For example: <code>d_shalayko</code>."
)

BOT_LOGIN_TAKEN = (
    "The login <b>{login}</b> already belongs to another Telegram account.\n"
    "If it's really yours, message the coworking admin."
)

BOT_WELCOME = (
    "You're all set, registered as <b>{login}</b>.\n\n"
    "Your PIN: <b>{pin}</b>\n"
    "Use it to book printers from the tablet on the wall. Write it down — "
    "I won't show it again, I can only issue a new one with /pin.\n\n"
    "{help}"
)

BOT_ALREADY_REGISTERED = (
    "{name}, you're already registered.\n\nLost your PIN? Get a new one with /pin.\n\n{help}"
)

BOT_PIN_CHANGED = "Your new PIN: <b>{pin}</b>\nThe old one stops working now."

BOT_NOT_REGISTERED = "Send /start first — I'll ask for your work login and give you a PIN."


# --- bot: status -------------------------------------------------------------

BOT_STATUS_MARKS = {
    PrinterStatus.FREE: "🟢",
    PrinterStatus.PRINTING: "🔴",
    PrinterStatus.DONE_WAIT: "🟡",
    PrinterStatus.BROKEN: "⚪️",
}

BOT_STATUS_MARK_UNKNOWN = "⚪️"

BOT_STATUS_WORDS = {
    PrinterStatus.FREE: "free",
    PrinterStatus.PRINTING: "printing",
    PrinterStatus.DONE_WAIT: "done, part still on the bed",
    PrinterStatus.BROKEN: "out of service",
}

BOT_STATUS_LINE = "{mark} <b>{printer}</b> — {word}"
BOT_STATUS_RESERVED = "{mark} <b>{printer}</b> — held for {name} until {time}"
BOT_STATUS_PRINTING = "    {name}, ~{left} left"
BOT_STATUS_DONE = "    {name}, finished {ago} ago"
BOT_STATUS_NOTE = "    {note}"

BOT_STATUS_QUEUE = "In line: {people}"
BOT_STATUS_QUEUE_EMPTY = "Nobody's in line"
BOT_STATUS_QUEUE_PERSON = "{position}. {name}"
BOT_STATUS_QUEUE_OFFERED = " (up next)"


# --- bot: my state -----------------------------------------------------------

BOT_MY_PRINTING = "You're printing on <b>{printer}</b>, ~{left} left.\nFree it up — /free"
BOT_MY_OFFERED = (
    "<b>{printer}</b> is yours to book — come over before {time}, "
    "after that it goes to the next person in line."
)
BOT_MY_IN_QUEUE = "You're in line, number {position}. Step out — /leave"
BOT_MY_NOTHING = "You've got nothing going right now. See the printers — /status"


# --- bot: the line -----------------------------------------------------------

BOT_QUEUE_JOINED = (
    "You're in line, number {position}.\n"
    "I'll message you when a printer frees up — you'll have 30 minutes to claim it."
)
BOT_QUEUE_ALREADY = "You're already in line, number {position}. Step out — /leave"
BOT_QUEUE_LEFT = "You've left the line."
BOT_QUEUE_REMOVED = "You were taken out of the line. Join again — /queue"

BOT_OFFER = (
    "<b>{printer}</b> is free and it's your turn.\n"
    "Book it before <b>{time}</b> — after that it goes to the next person in line.\n"
    "Book it from the tablet by the printers."
)
BOT_OFFER_EXPIRED = (
    "Your time on {printer} ran out — it went to the next person in line.\n"
    "Join the line again — /queue"
)
BOT_OFFER_NIGHT_HINT = "The clock stops overnight — your time picks up again in the morning."

# Used if the printer name somehow could not be found.
BOT_PRINTER_FALLBACK = "the printer"


# --- bot: printers -----------------------------------------------------------

BOT_OCCUPIED = (
    "You've booked <b>{printer}</b> for about {left} (until {time}).\n"
    "I'll remind you 15 minutes before it ends."
)
BOT_RELEASED = "{printer} is free again."

# No gender in the Russian original for a reason; keep it plain here too — the
# names are real and this line gets read every day.
BOT_RELEASED_BY_OTHER = (
    "Your print on <b>{printer}</b> was stopped ({name}) — the printer is free again.\n"
    "If you still need the part, it's waiting at the printer."
)

BOT_ALMOST_DONE = (
    "<b>{printer}</b>: your print finishes in about {left}.\n"
    "Come and grab the part — others are waiting for the printer."
)
BOT_FINISHED = (
    "<b>{printer}</b>: your print time is up.\n"
    "Check how it came out and take the part. Once the bed is clear, "
    "tap “I've taken my part” on the tablet or send /free."
)
BOT_CHECK_PRINTER = (
    "The print on <b>{printer}</b> should be finished by now ({name}). "
    "If the bed is clear, say so on the tablet — the printer becomes yours right away."
)
BOT_UNCLAIMED_OWNER = (
    "Your part has been sitting on <b>{printer}</b> for {ago} since the print finished. "
    "Please come and get it — others are waiting."
)
BOT_UNCLAIMED_QUEUE = (
    "The part on <b>{printer}</b> ({name}) has been sitting there for {ago}. "
    "You can take it off and free the printer — say so on the tablet."
)

BOT_CANCELLED_BY_ADMIN = "Your print on <b>{printer}</b> was cancelled."
BOT_CANCELLED_REASON = "\nReason: {reason}"
BOT_CANCELLED_TAIL = "\nIf you still need the part, it's waiting at the printer."

BOT_NOTHING_TO_FREE = "You haven't got a printer booked. See the printers — /status"


# --- refusals: printers and the line -----------------------------------------

ERR_PRINTER_NOT_FOUND = "No such printer"
ERR_PRINTER_BROKEN = "{printer} is out of service"
ERR_PRINTER_BUSY = "{printer} is already booked"
ERR_PRINTER_JUST_TAKEN = "Someone just booked {printer}"
ERR_PRINTER_NOT_PRINTING = "{printer} isn't printing"
ERR_PRINTER_NO_SESSION = "{printer} has no print running"
ERR_PRINTER_NOT_BROKEN = "{printer} isn't out of service"
ERR_PRINTER_RESERVED = "{printer} is held for the first person in line"
ERR_QUEUE_WAIT_YOUR_TURN = "There's a line — wait for your turn"

ERR_DURATION = "Pick between {min_minutes} minutes and {max_hours} hours"

ERR_USER_BUSY = "You've already got a printer booked"
ERR_USER_BUSY_FREE_FIRST = "You've already got a printer booked — free it up first"
ERR_ALREADY_IN_QUEUE = "You're already in line"
ERR_NOT_IN_QUEUE = "You're not in line"
ERR_OFFER_NOT_ACTIVE = "That offer is no longer valid"
ERR_OFFER_WINDOW_OPEN = "There's still time left on it"

# Cancellation reason written to the log when the admin gave none.
REASON_PRINTER_BROKEN = "printer taken out of service"


# --- refusals: access --------------------------------------------------------

ERR_KIOSK_ONLY = "Printers can only be booked from the tablet by the printers"
ERR_ADMIN_ONLY = "Admins only"
ERR_ADMIN_LOGIN_REQUIRED = "Please log in as an admin"
ERR_BAD_ENROLL_SECRET = "Wrong setup secret"
ERR_BAD_ADMIN_SECRET = "Wrong secret"

ERR_PIN_FORMAT = "The PIN is four digits"
ERR_PIN_WRONG = "Wrong PIN"
ERR_PIN_TAKEN = "That PIN is taken, pick another one"
ERR_PIN_NOT_PICKED = "Couldn't find a free PIN, please try again"
ERR_TOO_MANY_ATTEMPTS = "Too many tries — wait {seconds} s"

ERR_NO_ADMIN_IN_DB = (
    "No admin in the database yet. Create one: python -m app.cli make_admin <tg_id>"
)
ERR_REASON_REQUIRED = "Please say why"
ERR_USER_NOT_FOUND = "No such person"


# --- kiosk: banners after an action ------------------------------------------

FLASH_KIOSK = {
    "occupied": "Booked. Happy printing!",
    "released": "Printer freed up",
    "queued": "You're in line — watch for a message on Telegram",
    "left": "You've left the line",
}

FLASH_ADMIN = {
    "broken": "Printer taken out of service",
    "fixed": "Printer is back in service",
    "cancelled": "Print cancelled",
    "removed": "Taken out of the line",
    "pin_reset": "New PIN sent over Telegram",
}


# --- kiosk: print duration ---------------------------------------------------

DURATION_LABELS = {
    60: "1 h",
    120: "2 h",
    240: "4 h",
    480: "8 h",
    720: "12 h",
}
DURATION_NIGHT = "until morning"


# --- kiosk: confirmation screens ---------------------------------------------

CONFIRM_CLAIM_TITLE = "Got your part?"
CONFIRM_CLAIM_HINT = (
    "Once the bed is clear the printer goes free, and the next person in line gets a message."
)
CONFIRM_RELEASE_TITLE = "Free up the printer?"
CONFIRM_RELEASE_HINT = "The print will be stopped and marked as cancelled."
CONFIRM_RELEASE_SUBMIT = "Yes, free it up"

CONFIRM_QUEUE_JOIN_TITLE = "Join the line?"
CONFIRM_QUEUE_JOIN_HINT = (
    "When a printer frees up, the first person in line gets a message on Telegram "
    "and 30 minutes to claim it."
)
CONFIRM_QUEUE_JOIN_SUBJECT = "One line for all printers"
CONFIRM_QUEUE_JOIN_SUBMIT = "Join the line"

CONFIRM_QUEUE_LEAVE_TITLE = "Leave the line?"
CONFIRM_QUEUE_LEAVE_HINT = "You'll lose your place — joining again puts you at the back."
CONFIRM_QUEUE_LEAVE_SUBJECT = "The line"
CONFIRM_QUEUE_LEAVE_SUBMIT = "Step out"


# --- admin log ---------------------------------------------------------------
#
# Wording without gender: the log carries real names, and the same line gets
# read every day.

LOG_SESSION_STARTED = "{printer} — booked by {name}"
LOG_SESSION_COMPLETED = "{printer}: part collected"
LOG_SESSION_COMPLETED_BY = " (by {name})"
LOG_SESSION_CANCELLED = "{printer}: print cancelled"
LOG_SESSION_CANCELLED_BY = " by {name}"
LOG_SESSION_CANCEL_REASON = " — {reason}"

LOG_QUEUE_JOINED = "Joined the line: {name}"
LOG_QUEUE_OFFERED = "{printer} offered to {name}"
LOG_QUEUE_RESOLVED = "{word}: {name}"
LOG_QUEUE_TAKEN = "Offer taken"
LOG_QUEUE_EXPIRED = "Time ran out"
LOG_QUEUE_LEFT = "Left the line"


# --- other -------------------------------------------------------------------

API_TITLE = "3D printer booking"


# --- HTML templates ----------------------------------------------------------
#
# Available in Jinja as `t.<key>`, registered in app/api/kiosk.py.

UI = {
    # base.html
    "lang": LANG,
    "app_title": "Printers",
    "app_short_title": "Printers",
    "offline_banner": "No connection to the server. What's on screen may be out of date.",
    # _board.html
    "board_occupy_cta": "Book a printer",
    "board_free_of": "{free} of {total} free",
    "board_queue_cta": "Join the line",
    "board_all_busy": "all printers are busy",
    "tile_broken": "Out of service",
    "tile_printing": "Printing",
    "tile_until": "until {time}",
    "tile_release": "Free up",
    "tile_done_wait": "Ready to collect",
    "tile_done_at": "finished at {time}",
    "tile_claimed": "I've taken my part",
    "tile_reserved": "On hold",
    "tile_its_me": "That's me",
    "tile_free": "Free",
    "tile_occupy": "Book",
    "queue_offered": "up next",
    "queue_empty": "Nobody's in line",
    "queue_join": "Join the line",
    "queue_leave": "Leave the line",
    # _keypad.html
    "keypad_label": "PIN",
    "keypad_clear": "Clear",
    "keypad_hint": "Send /start to the bot to get a PIN",
    # confirm.html / occupy.html
    "cancel": "Cancel",
    "occupy_title": "Book {printer}",
    "occupy_heading": "{printer} — book it now",
    "occupy_duration_label": "How long?",
    "occupy_hint": (
        "A rough guess is fine — when the time is up the printer won't free itself, "
        "it'll just ask you to check the print."
    ),
    # error.html / offline.html
    "error_title": "That didn't work",
    "error_ok": "Got it",
    "offline_title": "No connection",
    "offline_line1": "No connection to the server.",
    "offline_line2": (
        "Booking is down right now — sort it out with each other, and record it here later."
    ),
    "offline_retry": "Try again",
    # admin_login.html
    "admin_login_title": "Admin login",
    "admin_login_hint": "The secret from",
    "admin_login_submit": "Log in",
    # admin.html
    "admin_title": "Admin",
    "admin_to_board": "Back to printers",
    "admin_printers": "Printers",
    "admin_owner_until": "{name}, until {time}",
    "admin_reserved": "held for {name} until {time}",
    "admin_fix": "Back in service",
    "admin_reason_placeholder": "why you're cancelling",
    "admin_cancel_print": "Cancel print",
    "admin_note_placeholder": "what broke",
    "admin_break": "Out of service",
    "admin_queue": "The line",
    "admin_remove": "Remove",
    "admin_empty": "Empty",
    "admin_people": "People ({count})",
    "admin_role": "admin",
    "admin_tg": "tg {chat_id}",
    "admin_new_pin": "New PIN",
    "admin_events": "Recent activity",
    "admin_no_events": "Nothing has happened yet",
}


# --- strings for the browser -------------------------------------------------
#
# Travel into the page as `data-texts` on <body> (base.html) and are used by
# app.js, where the time counters are redrawn every 30 seconds without asking
# the server.

JS = {
    "unit_minutes": UNIT_MINUTES,
    "unit_hours": UNIT_HOURS,
    "unit_hours_minutes": UNIT_HOURS_MINUTES,
    "eta_left": "~{left} left",
    "eta_over": "time's up, check the print",
    "done_ago": "finished {ago} ago",
}
