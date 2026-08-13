"""English interface texts.

Twin of `ru.py`: same names, same placeholders, translated values. Which one
gets loaded is decided by `app/texts/__init__.py` from `UI_LANG`.

**Vocabulary.** Picked once and used everywhere — the same action must not be
called two different things on the wall and in the bot:

* **book** a machine, not "take" or "occupy". The whole thing is a booking
  system, and "book / booked / free up" is how people already talk about
  meeting rooms;
* **machine** is the neutral word covering both printers and engravers; say
  **printer** or **engraver** only where the type is actually known;
* **line**, not "queue": you stand in it for a physical machine down the
  corridor, and "join the line" is what a person would actually say;
* **part** is the physical thing on the bed, **job** is the work the machine is
  doing. Makers talk that way: "your job is done, take your part off the bed";
* **out of service** for a broken machine — "under maintenance" sounds like
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

from app.enums import MachineKind, MachineStatus

LANG = "en"  # <html lang> attribute

# --- time --------------------------------------------------------------------

TIME_FORMAT = "%H:%M"
DATETIME_FORMAT = "%d %b %H:%M"

UNIT_MINUTES = "{minutes} min"
UNIT_HOURS = "{hours} h"
UNIT_HOURS_MINUTES = "{hours} h {minutes} min"

# Weekdays for the calendar strip, Monday first: `date.weekday()` indexes
# straight into this tuple.
WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
SCHEDULE_TODAY = "today"


# --- kinds of machine --------------------------------------------------------
#
# Section heading on the screen, the singular word for buttons, and the verb for
# the "busy" status. The singular carries its own article ("an engraver") so that
# "Book {kind}" and "in line for {kind}" both read right — Russian has no
# articles, so this stays inside the translation. A new kind in `MachineKind` has
# to appear in all three, or the wall ends up showing a raw enum value.

MACHINE_KIND_TITLE = {
    MachineKind.PRINTER: "Printers",
    MachineKind.ENGRAVER: "Engravers",
}

MACHINE_KIND_ONE = {
    MachineKind.PRINTER: "a printer",
    MachineKind.ENGRAVER: "an engraver",
}

# The word for "busy with a job". The other statuses don't depend on the kind.
MACHINE_BUSY_WORD = {
    MachineKind.PRINTER: "printing",
    MachineKind.ENGRAVER: "engraving",
}


# --- bot: help and commands --------------------------------------------------

BOT_HELP = (
    "Here's what I can do:\n"
    "/book — schedule and bookings ahead\n"
    "/status — how the machines are doing right now\n"
    "/my — my job, my booking and my place in line\n"
    "/queue — join the line\n"
    "/leave — leave the line\n"
    "/free — free up the machine I booked\n"
    "/pin — a new PIN for the tablet in the workshop\n\n"
    "You take a machine from the tablet in the workshop with your PIN, "
    "or right here in the bot's app (/book)."
)

# Command captions in the Telegram menu.
BOT_COMMAND_DESCRIPTIONS = {
    "book": "schedule and bookings",
    "status": "how the machines are doing",
    "my": "my job, booking and place in line",
    "queue": "join the line",
    "queue_printer": "line for a printer",
    "queue_engraver": "line for an engraver",
    "leave": "leave the line",
    "free": "free up my machine",
    "pin": "a new PIN",
    "help": "what I can do",
}


# --- bot: signing up ---------------------------------------------------------

BOT_ASK_LOGIN = (
    "Send me your work login — the same one as in your email, "
    "shaped like <b>n_username</b>.\n"
    "For example: <code>i_ivanov</code>.\n\n"
    "I'll hand you a PIN right after: that login is how you show up on the "
    "tablet in the workshop when you book a machine."
)

BOT_BAD_LOGIN = (
    "That doesn't look like a work login.\n"
    "It should look like <b>n_username</b> in Latin letters: a letter, an "
    "underscore, your surname. For example: <code>i_ivanov</code>."
)

BOT_LOGIN_TAKEN = (
    "The login <b>{login}</b> already belongs to another Telegram account.\n"
    "If it's really yours, message the coworking admin."
)

BOT_WELCOME = (
    "You're all set — signed up as <b>{login}</b>.\n\n"
    "Your PIN: <b>{pin}</b>\n"
    "You'll need it to book machines from the tablet in the workshop. "
    "Write it down — I won't show it again, I can only issue a new one via /pin.\n\n"
    "{help}"
)

BOT_ALREADY_REGISTERED = (
    "{name}, you're already signed up.\n\nForgot your PIN? Get a new one — /pin.\n\n{help}"
)

BOT_PIN_CHANGED = "Your new PIN: <b>{pin}</b>\nThe old one stops working now."

BOT_NAME_CHANGED = (
    "An admin fixed your login: it was <b>{previous}</b>, now it's <b>{login}</b>.\n"
    "That's what everyone sees on the tablet in the workshop. Your PIN hasn't changed.\n"
    "If this looks wrong, message the coworking admin."
)

BOT_NOT_REGISTERED = "Send /start first — I'll ask for your work login and give you a PIN."


# --- bot: status -------------------------------------------------------------

BOT_STATUS_MARKS = {
    MachineStatus.FREE: "🟢",
    MachineStatus.PRINTING: "🔴",
    MachineStatus.DONE_WAIT: "🟡",
    MachineStatus.BROKEN: "⚪️",
}

BOT_STATUS_MARK_UNKNOWN = "⚪️"

# The word for "busy" comes from MACHINE_BUSY_WORD, keyed by the kind.
BOT_STATUS_WORDS = {
    MachineStatus.FREE: "free",
    MachineStatus.DONE_WAIT: "done, part still on the bed",
    MachineStatus.BROKEN: "out of service",
}

BOT_STATUS_SECTION = "<b>{title}</b>"
BOT_STATUS_LINE = "{mark} <b>{machine}</b> — {word}"
BOT_STATUS_RESERVED = "{mark} <b>{machine}</b> — held for {name} until {time}"
BOT_STATUS_BUSY = "    {name}, ~{left} left"
BOT_STATUS_DONE = "    {name}, finished {ago} ago"
BOT_STATUS_NOTE = "    {note}"

BOT_STATUS_QUEUE = "In line: {people}"
BOT_STATUS_QUEUE_EMPTY = "Nobody's in line"
BOT_STATUS_QUEUE_PERSON = "{position}. {name}"
BOT_STATUS_QUEUE_OFFERED = " (up next)"

BOT_STATUS_PARK_EMPTY = "There aren't any machines yet."


# --- bot: my state -----------------------------------------------------------

BOT_MY_BUSY = "You've got <b>{machine}</b>, ~{left} left.\nFree it up — /free"
BOT_MY_OFFERED = (
    "<b>{machine}</b> is yours to book — come over before {time}, "
    "after that it goes to the next person in line."
)
BOT_MY_IN_QUEUE = "You're in line for {kind}, number {position}. Step out — /leave"
BOT_MY_NOTHING = "You've got nothing going right now. See the machines — /status"


# --- bot: the line -----------------------------------------------------------

# There are as many lines as there are kinds: waiting for an engraver and
# waiting for a printer are separate, so the command needs a kind.
BOT_QUEUE_PICK = "What are you waiting for?\n{options}"
BOT_QUEUE_PICK_OPTION = "{command} — {title}"

BOT_QUEUE_JOINED = (
    "You're in line for {kind}, number {position}.\n"
    "I'll message you when one frees up — you'll have 30 minutes to claim it."
)
BOT_QUEUE_ALREADY = "You're already in line, number {position}. Step out — /leave"
BOT_QUEUE_LEFT = "You've left the line."
BOT_QUEUE_REMOVED = "You were taken out of the line. Join again — /queue"

BOT_OFFER = (
    "<b>{machine}</b> is free and it's your turn.\n"
    "Book it before <b>{time}</b> — after that it goes to the next person in line.\n"
    "Book it from the tablet in the workshop."
)
BOT_OFFER_EXPIRED = (
    "Your time on {machine} ran out — it went to the next person in line.\n"
    "Join the line again — /queue"
)
BOT_OFFER_NIGHT_HINT = "The clock stops overnight — your time picks up again in the morning."

# Used if the machine name somehow could not be found.
BOT_MACHINE_FALLBACK = "the machine"


# --- bot: machines -----------------------------------------------------------

BOT_OCCUPIED = (
    "You've booked <b>{machine}</b> for about {left} (until {time}).\n"
    "I'll remind you 15 minutes before it ends."
)
BOT_RELEASED = "{machine} is free again."

# No gender in the Russian original for a reason; keep it plain here too — the
# names are real and this line gets read every day.
BOT_RELEASED_BY_OTHER = (
    "Your job on <b>{machine}</b> was stopped ({name}) — the machine is free again.\n"
    "If you still need the part, it's waiting at the machine."
)

BOT_ALMOST_DONE = (
    "<b>{machine}</b>: your job finishes in about {left}.\n"
    "Come and grab the part — others are waiting for the machine."
)
BOT_FINISHED = (
    "<b>{machine}</b>: your time is up.\n"
    "Check how it came out and take the part. Once the bed is clear, "
    "tap “I've taken my part” on the tablet or send /free."
)
BOT_CHECK_MACHINE = (
    "The job on <b>{machine}</b> should be finished by now ({name}). "
    "If the bed is clear, say so on the tablet — the machine becomes yours right away."
)
BOT_UNCLAIMED_OWNER = (
    "Your part has been sitting on <b>{machine}</b> for {ago} since the job finished. "
    "Please come and get it — others are waiting."
)
BOT_UNCLAIMED_QUEUE = (
    "The part on <b>{machine}</b> ({name}) has been sitting there for {ago}. "
    "You can take it off and free the machine — say so on the tablet."
)

BOT_CANCELLED_BY_ADMIN = "Your job on <b>{machine}</b> was cancelled."
BOT_CANCELLED_REASON = "\nReason: {reason}"
BOT_CANCELLED_TAIL = "\nIf you still need the part, it's waiting at the machine."

BOT_NOTHING_TO_FREE = "You haven't got a machine booked. See the machines — /status"


# --- bot: bookings ahead -----------------------------------------------------

BOT_BOOKED = (
    "Booked: <b>{machine}</b>\n{start} — {end}\n"
    "I'll remind you an hour before. Cancel it — /my"
)

BOT_BOOKING_SOON = (
    "Your booking starts in {left}: <b>{machine}</b> at {time}.\n"
    "Come to the machine and take it from the tablet or in the app."
)

BOT_BOOKING_AFTER_YOU = (
    "Someone has <b>{machine}</b> booked at {time}, right after you.\n"
    "Please collect your part before then so they don't arrive to a busy table."
)

BOT_BOOKING_STARTED = (
    "Your slot on <b>{machine}</b> has started and the machine is free.\n"
    "Take it before {time} or the booking is dropped."
)

BOT_BOOKING_STARTED_BUSY = (
    "Your slot on <b>{machine}</b> has started, but someone's part is still on the table.\n"
    "You can clear it yourself — tap \"Got my part\" and take the machine. "
    "While the table is busy your booking is not dropped."
)

BOT_BOOKING_MISSED = (
    "Your booking on <b>{machine}</b> is dropped: the machine sat free and you "
    "didn't take it within {minutes} min. Book again — /book"
)

BOT_BOOKING_CANCELLED = "The booking on <b>{machine}</b> ({start}) is cancelled."

BOT_BOOKING_CANCELLED_BY_ADMIN = "Your booking on <b>{machine}</b> ({start}) was cancelled."

BOT_MY_BOOKING = "Booking: <b>{machine}</b>, {start} — {end}"

BOT_BOOK_INVITE = "The schedule and your bookings live in the app:"
BOT_BOOK_BUTTON = "Open the schedule"
BOT_BOOK_NO_APP = (
    "The app isn't set up: the server has no https address, so Telegram won't "
    "open a mini app. Book from the tablet in the workshop instead."
)


# --- refusals: machines and the line -----------------------------------------

ERR_MACHINE_NOT_FOUND = "No such machine"
ERR_MACHINE_BROKEN = "{machine} is out of service"
ERR_MACHINE_BUSY = "{machine} is already booked"
ERR_MACHINE_JUST_TAKEN = "Someone just booked {machine}"
ERR_MACHINE_NOT_WORKING = "{machine} isn't running anything"
ERR_MACHINE_NO_SESSION = "{machine} has no job running"
ERR_MACHINE_NOT_BROKEN = "{machine} isn't out of service"
ERR_MACHINE_RESERVED = "{machine} is held for the first person in line"
ERR_MACHINE_RELEASE_FORBIDDEN = (
    "Only the person who started this job can stop it while it is running"
)
ERR_QUEUE_WAIT_YOUR_TURN = "There's a line — wait for your turn"

ERR_DURATION = "Pick between {min_minutes} minutes and {max_hours} hours"

ERR_MACHINE_BOOKED_NOW = "{machine} is booked until {time}"
ERR_MACHINE_BOOKED_LATER = (
    "{machine} is booked from {time} — right now you can take it for {minutes} min at most"
)


# --- refusals: bookings ahead ------------------------------------------------

ERR_RESERVATION_DURATION = "Book between {min_minutes} minutes and {max_hours} hours"
ERR_RESERVATION_NOT_ALIGNED = "A booking starts on a {step}-minute mark"
ERR_RESERVATION_PAST = "That time has already passed"
ERR_RESERVATION_HORIZON = "You can book up to {days} days ahead"
ERR_RESERVATION_OVERLAP = "{machine} is already booked for that time (from {time})"
ERR_RESERVATION_JUST_BOOKED = "{machine} has just been booked for that time"
ERR_RESERVATION_BUSY = "{machine} is working until {time} — pick a later time"
ERR_ALREADY_BOOKED = "You already have a booking. Cancel it to book another slot"
ERR_RESERVATION_NOT_FOUND = "No such booking, or it is already closed"
ERR_RESERVATION_FORBIDDEN = "Only an admin can cancel someone else's booking"
ERR_RESERVATION_WINDOW_OPEN = "The booking slot hasn't run out yet"
ERR_RESERVATION_MACHINE_BUSY = "{machine} is busy — the booking waits for the table"
ERR_RESERVATION_WORK_HOURS = "A booking can only start during opening hours: {hours}"

ERR_WORK_HOURS_ORDER = "Closing time must be later than opening (00:00 — round the clock)"
ERR_WORK_HOURS_FORMAT = "“{value}” doesn't look like a time — try something like 08:00"

ERR_USER_BUSY = "You've already got a machine booked"
ERR_USER_BUSY_FREE_FIRST = "You've already got a machine booked — free it up first"
ERR_ALREADY_IN_QUEUE = "You're already in line"
ERR_NOT_IN_QUEUE = "You're not in line"
ERR_OFFER_NOT_ACTIVE = "That offer is no longer valid"
ERR_OFFER_WINDOW_OPEN = "There's still time left on it"

# Cancellation reason written to the log when the admin gave none.
REASON_MACHINE_BROKEN = "machine taken out of service"


# --- refusals: the park ------------------------------------------------------

ERR_MACHINE_NAME_EMPTY = "Give the machine a name"
ERR_MACHINE_NAME_LONG = "A name longer than {limit} characters won't fit"
ERR_MACHINE_NAME_TAKEN = "The name {name} already belongs to another machine"
ERR_MACHINE_KIND_UNKNOWN = "Unknown kind of machine: {kind}"
ERR_MACHINE_HAS_HISTORY = (
    "{machine} can't be deleted: the log has {sessions} job(s) and {offers} "
    "offer(s) behind it. If the machine is gone for good, take it out of "
    "service instead — that keeps the log intact."
)


# --- refusals: access --------------------------------------------------------

ERR_KIOSK_ONLY = (
    "Machines can be taken from the tablet in the workshop or in the bot's app (/book)"
)
ERR_APP_BAD_INIT_DATA = "Couldn't confirm the app was opened from Telegram"
ERR_APP_SESSION_REQUIRED = "Open the schedule again with /book in the chat with the bot"
ERR_APP_NOT_REGISTERED = "Send /start to the bot — it asks for your login and hands you a PIN"
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
ERR_LOGIN_FORMAT = "A login looks like n_username in Latin letters, e.g. i_ivanov"
ERR_LOGIN_TAKEN = "The login {login} already belongs to someone else"


# --- kiosk: banners after an action ------------------------------------------

FLASH_KIOSK = {
    "occupied": "Booked. Have a good one!",
    "released": "Machine freed up",
    "queued": "You're in line — watch for a message on Telegram",
    "left": "You've left the line",
    "booked": "Booked — we'll remind you an hour before",
    "booking_cancelled": "Booking cancelled",
}

FLASH_ADMIN = {
    "broken": "Machine taken out of service",
    "fixed": "Machine is back in service",
    "cancelled": "Job cancelled",
    "removed": "Taken out of the line",
    "pin_reset": "New PIN sent over Telegram",
    "renamed": "Login updated",
    "machine_added": "Machine added",
    "machine_renamed": "Machine renamed",
    "machine_removed": "Machine deleted",
    "booking_cancelled": "Booking cancelled",
    "hours_saved": "Opening hours saved",
}


# --- kiosk: job duration -----------------------------------------------------

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
    "Once the bed is clear the machine goes free, and the next person in line gets a message."
)
CONFIRM_RELEASE_TITLE = "Free up the machine?"
CONFIRM_RELEASE_HINT = "The job will be stopped and marked as cancelled."
CONFIRM_RELEASE_SUBMIT = "Yes, free it up"

CONFIRM_QUEUE_JOIN_TITLE = "Join the line?"
CONFIRM_QUEUE_JOIN_HINT = (
    "When a machine frees up, the first person in line gets a message on Telegram "
    "and 30 minutes to claim it."
)
CONFIRM_QUEUE_JOIN_SUBJECT = "Line for: {title}"
CONFIRM_QUEUE_JOIN_SUBMIT = "Join the line"

CONFIRM_QUEUE_LEAVE_TITLE = "Leave the line?"
CONFIRM_QUEUE_LEAVE_HINT = "You'll lose your place — joining again puts you at the back."
CONFIRM_QUEUE_LEAVE_SUBJECT = "The line"
CONFIRM_QUEUE_LEAVE_SUBMIT = "Step out"


# --- admin log ---------------------------------------------------------------
#
# Wording without gender: the log carries real names, and the same line gets
# read every day.

LOG_SESSION_STARTED = "{machine} — booked by {name}"
LOG_SESSION_COMPLETED = "{machine}: part collected"
LOG_SESSION_COMPLETED_BY = " (by {name})"
LOG_SESSION_CANCELLED = "{machine}: job cancelled"
LOG_SESSION_CANCELLED_BY = " by {name}"
LOG_SESSION_CANCEL_REASON = " — {reason}"

LOG_RESERVATION_BOOKED = "{machine} booked for {start}: {name}"
LOG_RESERVATION_TAKEN = "{machine}: arrived for the booking — {name}"
LOG_RESERVATION_EXPIRED = "{machine}: booking dropped, no-show — {name}"
LOG_RESERVATION_CANCELLED = "{machine}: booking for {start} cancelled — {name}"

LOG_QUEUE_JOINED = "Joined the line for {kind}: {name}"
LOG_QUEUE_OFFERED = "{machine} offered to {name}"
LOG_QUEUE_RESOLVED = "{word}: {name}"
LOG_QUEUE_TAKEN = "Offer taken"
LOG_QUEUE_EXPIRED = "Time ran out"
LOG_QUEUE_LEFT = "Left the line"


# --- other -------------------------------------------------------------------

API_TITLE = "Workshop machine booking"


# --- HTML templates ----------------------------------------------------------
#
# Available in Jinja as `t.<key>`, registered in app/api/render.py.

UI = {
    # base.html
    "lang": LANG,
    "app_title": "Workshop",
    "app_short_title": "Workshop",
    "offline_banner": "No connection to the server. What's on screen may be out of date.",
    # _board.html
    "board_occupy_cta": "Book {kind}",
    "board_free_of": "{free} of {total} free",
    "board_queue_cta": "Join the line",
    "board_all_busy": "all busy",
    "board_park_empty": "No machines yet — add them in the admin panel",
    "tile_broken": "Out of service",
    "tile_busy": "Busy",
    "tile_until": "until {time}",
    "tile_release": "Free up",
    "tile_done_wait": "Ready to collect",
    "tile_done_at": "finished at {time}",
    "tile_claimed": "I've taken my part",
    "tile_reserved": "On hold",
    "tile_its_me": "That's me",
    "tile_free": "Free",
    "tile_occupy": "Book",
    "tile_booked": "Booked",
    "tile_booked_from": "booked from {time}",
    "queue_offered": "up next",
    "queue_empty": "Nobody's in line",
    "queue_join": "Join the line",
    "queue_leave": "Leave the line",
    "board_schedule_cta": "Schedule",
    "board_show_all": "All equipment",
    "board_show_mine": "Just my machine",
    # _keypad.html
    "keypad_label": "PIN",
    "keypad_clear": "Clear",
    "keypad_hint": "Send /start to the bot to get a PIN",
    # confirm.html / occupy.html
    "cancel": "Cancel",
    # On a screen where nothing has started, "Cancel" lies — there's nothing to cancel.
    "back": "Back",
    "occupy_title": "Book {machine}",
    "occupy_heading": "{machine} — book it now",
    "occupy_duration_label": "How long?",
    "occupy_hint": (
        "A rough guess is fine — when the time is up the machine won't free itself, "
        "it'll just ask you to check how it came out."
    ),
    "occupy_submit": "Book now",
    # schedule.html
    "schedule_title": "Schedule: {title}",
    "schedule_heading": "Schedule: {title}",
    "schedule_day_label": "Day",
    "schedule_free": "free",
    "schedule_past": "past",
    "schedule_busy": "working",
    "schedule_booked": "booked",
    "schedule_mine": "my booking",
    "schedule_broken": "out of service",
    "schedule_empty": "No machines of this kind yet",
    "schedule_hint": "Tap a free hour — that's when your booking starts",
    "schedule_work_hours": "Open {hours}",
    "schedule_my_bookings": "My bookings",
    # book.html
    "book_title": "Book {machine}",
    "book_heading": "{machine} — book ahead",
    "book_when": "{day}, {time}",
    "book_duration_label": "For how long",
    "book_submit": "Book it",
    "book_hint": (
        "Take the machine within {grace} min of the start — otherwise the booking "
        "is dropped and goes to the line. We'll remind you an hour before."
    ),
    "book_no_slots": "Too little time before the next booking — pick another hour",
    "book_cancel_hint": (
        "The hour frees up and goes to the line. "
        "You can only cancel your own booking — your PIN is needed."
    ),
    "book_booked_until": "Booked until {time}",
    # my.html
    "my_title": "My bookings",
    "my_heading": "My bookings",
    "my_empty": "No bookings yet. Open the schedule and pick a free hour.",
    "my_when": "{start} — {end}",
    "my_cancel": "Cancel booking",
    "my_schedule": "To the schedule",
    "my_state_busy": "You've got {machine} until {time}",
    "my_state_queue": "In line for {kind}, number {position}",
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
    "admin_to_board": "Back to the workshop",
    "admin_tab_summary": "Summary",
    "admin_tab_machines": "Machines",
    "admin_tab_hours": "Opening hours",
    "admin_bookings": "Bookings",
    "admin_bookings_none": "No bookings",
    "admin_booking_row": "{machine}: {start} — {end}",
    "admin_booking_cancel": "Cancel",
    "admin_machines": "Machines",
    "admin_owner_until": "{name}, until {time}",
    "admin_reserved": "held for {name} until {time}",
    "admin_fix": "Back in service",
    "admin_reason_placeholder": "why you're cancelling",
    "admin_cancel_work": "Cancel job",
    "admin_note_placeholder": "what broke",
    "admin_break": "Out of service",
    "admin_queue": "The line",
    "admin_queue_of": "Line: {title}",
    "admin_remove": "Remove",
    "admin_empty": "Empty",
    "admin_people": "People ({count})",
    "admin_role": "admin",
    "admin_tg": "tg {chat_id}",
    "admin_rename": "Rename",
    "admin_new_pin": "New PIN",
    "admin_events": "Recent activity",
    "admin_no_events": "Nothing has happened yet",
    # miniapp
    "app_loading": "Opening…",
    "app_outside_telegram_title": "Open it from Telegram",
    "app_outside_telegram_hint": (
        "This is the bot's mini app: the schedule and bookings open with /book "
        "in the chat with the bot. You can still watch the statuses here."
    ),
    "app_test_title": "Test sign-in",
    "app_test_hint": (
        "MINIAPP_OPEN_ACCESS is on: the Telegram signature isn't checked and "
        "anyone can sign in as anyone. Turn the flag off when you're done."
    ),
    "app_test_no_people": "Nobody in the database yet — send /start to the bot",
    "app_not_registered_title": "You're not signed up yet",
    "app_not_registered_hint": (
        "Send /start to the bot — it asks for your work login and hands you a PIN. "
        "The schedule opens after that."
    ),
    "app_nav_board": "Workshop",
    "app_nav_my": "My bookings",
    # admin_machines.html
    "admin_machines_title": "Machines",
    "admin_machines_add": "Add a machine",
    "admin_machines_name_placeholder": "name, e.g. P2S #3",
    "admin_machines_kind_label": "Kind",
    "admin_machines_add_submit": "Add",
    "admin_machines_none": "No machines of this kind",
    "admin_machines_delete": "Delete",
    "admin_machines_history": "in the log: {sessions} job(s), {offers} offer(s)",
    "admin_machines_no_history": "no history — safe to delete",
    "admin_machines_hint": (
        "The name shows up on the workshop screen and in every bot message. "
        "The kind is fixed once created: the log already points at this machine. "
        "Don't delete a machine that's gone for good — take it out of service on "
        "the Summary tab instead, so it stays in the log but can't be booked."
    ),
    # admin_hours.html
    "admin_hours_title": "Opening hours",
    "admin_hours_block": "When the workshop is open",
    "admin_hours_opens": "Opens",
    "admin_hours_closes": "Closes",
    "admin_hours_submit": "Save",
    "admin_hours_current": "Right now: {hours}",
    "admin_hours_hint": (
        "A booking can only start during these hours — the schedule shows them "
        "and nothing else. When it ends is not limited: a print started at "
        "19:00 runs through the night, the part gets picked up after opening. "
        "For a round-the-clock workshop set both to 00:00. "
        "Existing bookings are left alone: if you moved the hours, check the "
        "list of bookings on the Summary tab."
    ),
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
    "eta_over": "time's up, check how it came out",
    "done_ago": "finished {ago} ago",
}
