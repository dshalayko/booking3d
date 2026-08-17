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

from app.enums import MachineKind, MachineStatus, RoomKind

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


# --- kinds of room -----------------------------------------------------------
#
# Group heading in the admin panel and the singular word for a list row.

ROOM_KIND_TITLE = {
    RoomKind.WORKSHOP: "Workshops",
    RoomKind.MEETING: "Meeting rooms",
}

ROOM_KIND_ONE = {
    RoomKind.WORKSHOP: "workshop",
    RoomKind.MEETING: "meeting room",
}

# The room's mark in the /status block heading. A message covers several rooms,
# and the mark is what lets the eye find where a block starts without reading.
ROOM_KIND_MARK = {
    RoomKind.WORKSHOP: "🛠️",
    RoomKind.MEETING: "💬",
}


# --- kinds of machine --------------------------------------------------------
#
# Section heading on the screen, the singular word for buttons, the verb for the
# "busy" status, and the words for a job that has finished. The singular carries
# its own article ("an engraver") so that "Book {kind}" and "in line for {kind}"
# both read right — Russian has no articles, so this stays inside the
# translation. A new kind in `MachineKind` has to appear in all of these dicts,
# or the wall ends up showing a raw enum value.

MACHINE_KIND_TITLE = {
    MachineKind.PRINTER: "Printers",
    MachineKind.ENGRAVER: "Engravers",
    MachineKind.MEETING_ROOM: "Meeting room",
}

MACHINE_KIND_ONE = {
    MachineKind.PRINTER: "a printer",
    MachineKind.ENGRAVER: "an engraver",
    MachineKind.MEETING_ROOM: "the meeting room",
}

# The word for "busy with a job". The other statuses don't depend on the kind.
MACHINE_BUSY_WORD = {
    MachineKind.PRINTER: "printing",
    MachineKind.ENGRAVER: "engraving",
    MachineKind.MEETING_ROOM: "in use",
}

# "The job is over but the machine isn't free yet" — rule 8. On a printer that's
# a part left on the bed, in a meeting room it's people who haven't walked out:
# different words for one and the same state.

# Status on the tile.
MACHINE_DONE_STATUS = {
    MachineKind.PRINTER: "Ready to collect",
    MachineKind.ENGRAVER: "Ready to collect",
    MachineKind.MEETING_ROOM: "Time's up",
}

# The button that frees the machine from that state.
MACHINE_DONE_ACTION = {
    MachineKind.PRINTER: "I've taken my part",
    MachineKind.ENGRAVER: "I've taken my part",
    MachineKind.MEETING_ROOM: "The room is empty",
}

# The same state in a bot message.
MACHINE_DONE_WORD = {
    MachineKind.PRINTER: "done, part still on the bed",
    MachineKind.ENGRAVER: "done, part still on the bed",
    MachineKind.MEETING_ROOM: "time's up, the room isn't free yet",
}

# Heading and hint on the confirmation screen.
MACHINE_DONE_CONFIRM = {
    MachineKind.PRINTER: "Got your part?",
    MachineKind.ENGRAVER: "Got your part?",
    MachineKind.MEETING_ROOM: "Is the room empty?",
}

MACHINE_DONE_HINT = {
    MachineKind.PRINTER: (
        "Once the bed is clear the machine goes free, and the next person in line gets a message."
    ),
    MachineKind.ENGRAVER: (
        "Once the bed is clear the machine goes free, and the next person in line gets a message."
    ),
    MachineKind.MEETING_ROOM: (
        "Once the room is empty it goes free, and the next person in line gets a message."
    ),
}

# What to do when the time is nearly up or already over. These go into
# `BOT_ALMOST_DONE`, `BOT_FINISHED`, `BOT_CHECK_MACHINE` and `BOT_UNCLAIMED_*`:
# on a printer it's about the part, in a meeting room it's about walking out.

MACHINE_ALMOST_DONE_HINT = {
    MachineKind.PRINTER: "Come and grab the part — others are waiting for the machine.",
    MachineKind.ENGRAVER: "Come and grab the part — others are waiting for the machine.",
    MachineKind.MEETING_ROOM: "Time to wrap up — others are waiting for the room.",
}

MACHINE_FINISHED_HINT = {
    MachineKind.PRINTER: (
        "Check how it came out and take the part. Once the bed is clear, "
        "tap “I've taken my part” on the tablet or send /free."
    ),
    MachineKind.ENGRAVER: (
        "Check how it came out and take the part. Once the bed is clear, "
        "tap “I've taken my part” on the tablet or send /free."
    ),
    MachineKind.MEETING_ROOM: (
        "When you walk out, tap “The room is empty” on the tablet or send /free."
    ),
}

MACHINE_CHECK_HINT = {
    MachineKind.PRINTER: (
        "If the bed is clear, say so on the tablet — the machine becomes yours right away."
    ),
    MachineKind.ENGRAVER: (
        "If the bed is clear, say so on the tablet — the machine becomes yours right away."
    ),
    MachineKind.MEETING_ROOM: (
        "If the room is empty, say so on the tablet — it becomes yours right away."
    ),
}

MACHINE_UNCLAIMED_OWNER_HINT = {
    MachineKind.PRINTER: "Please come and get it — others are waiting.",
    MachineKind.ENGRAVER: "Please come and get it — others are waiting.",
    MachineKind.MEETING_ROOM: "Say on the tablet that you're out — others are waiting.",
}

MACHINE_UNCLAIMED_QUEUE_HINT = {
    MachineKind.PRINTER: "You can take it off yourself — say so on the tablet.",
    MachineKind.ENGRAVER: "You can take it off yourself — say so on the tablet.",
    MachineKind.MEETING_ROOM: "If the room is empty, say so on the tablet.",
}


# --- bot: help and commands --------------------------------------------------
#
# Bot messages get read on the move, at a glance, most often in the chat list
# where only the first line shows. So every BOT_* message has one shape:
#
#     <mark> what happened — with the machine's name in <b>
#     details: the time, who, how much is left
#     <blank line>
#     what to do and which command does it
#
# One mark, at the start of the first line: a second one in the same message
# competes with the first for attention. The set of marks is shared across every
# message, otherwise the same event ends up looking different in each place:
#
#     🔔 act now (an offer, a booking that just started)
#     ⏰ a reminder about what's coming
#     ⌛️ time ran out, the chance is gone
#     ⏳ waiting: the line, or "nearly over"
#     🟢 free  🔴 taken  🟡 the job is over but nobody freed it
#     📅 booking   🏁 the job finished   👀 go check someone else's machine
#     ⚠️ something's off   ⛔️ an admin cancelled it   ✅ done
#     🔑 PIN   👋 hello   ✏️ renamed   🚪 stepping out   👌 all quiet
#
# The blank line separates "what happened" from "what to do": without it the
# command gets lost in the text, and with a break inside the paragraph the
# sentence gets torn instead. Breaks are put here by hand — Telegram won't.

BOT_HELP = (
    "Here's what I can do:\n\n"
    "📅 /book — schedule and bookings ahead\n"
    "📊 /status — how the machines are doing right now\n"
    "👤 /my — my job, my booking and my place in line\n"
    "⏳ /queue — join the line\n"
    "🚪 /leave — leave the line\n"
    "🟢 /free — free up the machine I booked\n"
    "🔑 /pin — a new PIN for the tablet in the room\n\n"
    "You take a machine from the tablet in the room with your PIN, "
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
    "queue_meeting_room": "line for the meeting room",
    "leave": "leave the line",
    "free": "free up my machine",
    "pin": "a new PIN",
    "help": "what I can do",
}


# --- bot: signing up ---------------------------------------------------------

BOT_ASK_LOGIN = (
    "👋 Let's get you set up.\n\n"
    "Send me your work login — the same one as in your email, "
    "shaped like <b>n_username</b>.\n"
    "For example: <code>i_ivanov</code>\n\n"
    "I'll hand you a PIN right after: that login is how you show up on the "
    "tablet in the room when you book a machine."
)

BOT_BAD_LOGIN = (
    "⚠️ That doesn't look like a work login.\n\n"
    "It should look like <b>n_username</b> in Latin letters: a letter, an "
    "underscore, your surname.\n"
    "For example: <code>i_ivanov</code>"
)

BOT_LOGIN_TAKEN = (
    "⛔️ The login <b>{login}</b> already belongs to another Telegram account.\n\n"
    "If it's really yours, message the coworking admin."
)

BOT_WELCOME = (
    "✅ You're all set — signed up as <b>{login}</b>.\n\n"
    "Your PIN: <b>{pin}</b>\n"
    "You'll need it to book machines from the tablet in the room.\n"
    "I've pinned this message to the top of the chat so the PIN stays at hand. "
    "I won't show it again, I can only issue a new one via /pin.\n\n"
    "{help}"
)

BOT_ALREADY_REGISTERED = (
    "👌 {name}, you're already signed up.\n\n"
    "Forgot your PIN? Get a new one — /pin.\n\n"
    "{help}"
)

BOT_PIN_CHANGED = (
    "🔑 Your new PIN: <b>{pin}</b>\n\n"
    "The old one stops working now. This message is pinned at the top of the chat "
    "in place of the previous one."
)

BOT_NAME_CHANGED = (
    "✏️ An admin fixed your login.\n\n"
    "It was <b>{previous}</b>, now it's <b>{login}</b>.\n"
    "That's what everyone sees on the tablet in the room. Your PIN hasn't changed.\n\n"
    "If this looks wrong, message the coworking admin."
)

BOT_NOT_REGISTERED = (
    "👋 Send /start first — I'll ask for your work login and give you a PIN."
)


# --- bot: status -------------------------------------------------------------

BOT_STATUS_MARKS = {
    MachineStatus.FREE: "🟢",
    MachineStatus.PRINTING: "🔴",
    MachineStatus.DONE_WAIT: "🟡",
    MachineStatus.BROKEN: "⚪️",
}

BOT_STATUS_MARK_UNKNOWN = "⚪️"

# The words for "busy" and "done" come from MACHINE_BUSY_WORD and
# MACHINE_DONE_WORD, keyed by the kind: they depend on whether it's a printer or
# a room.
BOT_STATUS_WORDS = {
    MachineStatus.FREE: "free",
    MachineStatus.BROKEN: "out of service",
}

# The room is the heading of a block, the kind of machine is a sub-heading
# inside it. No sub-heading when the room holds a single kind: in the meeting
# room "Oak" a line saying "Meeting room" is the same thing twice.
#
# The detail line starts with "└" rather than a run of spaces: different
# Telegram clients treat leading spaces differently, while the corner reads at
# once as a continuation of the line above.
BOT_STATUS_ROOM = "{mark} <b>{name}</b>"
BOT_STATUS_SECTION = "<i>{title}</i>"
BOT_STATUS_LINE = "{mark} <b>{machine}</b> — {word}"
BOT_STATUS_RESERVED = "{mark} <b>{machine}</b> — held for {name} until {time}"
BOT_STATUS_BUSY = "└ {name}, ~{left} left"
BOT_STATUS_DONE = "└ {name}, finished {ago} ago"
BOT_STATUS_NOTE = "└ {note}"

BOT_STATUS_QUEUE = "⏳ In line: {people}"
BOT_STATUS_QUEUE_EMPTY = "⏳ Nobody's in line"
BOT_STATUS_QUEUE_PERSON = "{position}. {name}"
BOT_STATUS_QUEUE_OFFERED = " (up next)"

BOT_STATUS_PARK_EMPTY = "There aren't any machines yet."


# --- bot: my state -----------------------------------------------------------

# You can have one of each of these per room (rules 2 and 13), so every line
# names the room: "P2S #1" tells you where to go, "number 3 in line" doesn't.

BOT_MY_BUSY = (
    "🔴 You've got <b>{machine}</b> ({room}), ~{left} left.\nFree it up — /free"
)
BOT_MY_OFFERED = (
    "🔔 <b>{machine}</b> ({room}) is yours to book — come over before {time}, "
    "after that it goes to the next person in line."
)
BOT_MY_IN_QUEUE = "⏳ In line for {kind} ({room}), number {position}.\nStep out — /leave"
BOT_MY_NOTHING = (
    "👌 You've got nothing going right now.\n\nSee the machines — /status"
)


# --- bot: the line -----------------------------------------------------------

# There are as many lines as there are (room, kind) pairs: waiting for an
# engraver and waiting for a printer are separate, and waiting for a printer in
# another room is a third one. While there is only one pair /queue is
# unambiguous; once there are several, a command can't list them — a screen can.
BOT_QUEUE_PICK = (
    "⏳ There's more than one line — pick a room in the app (/book) "
    "or on the tablet:\n\n{options}"
)
BOT_QUEUE_PICK_OPTION = "• {room} — {title}"
BOT_QUEUE_PICK_KINDS = "⏳ What are you waiting for?\n\n{options}"
BOT_QUEUE_PICK_KIND = "{command} — {title}"

BOT_QUEUE_LEAVE_PICK = (
    "🚪 You're in several lines — step out of the right one in the app (/book) "
    "or on the tablet:\n\n{options}"
)

BOT_QUEUE_JOINED = (
    "⏳ You're in line for {kind} ({room}), number {position}.\n\n"
    "I'll message you when one frees up — you'll have 30 minutes to claim it."
)
BOT_QUEUE_ALREADY = "⏳ You're already in line, number {position}.\n\nStep out — /leave"
BOT_QUEUE_LEFT = "🚪 You've left the line."
BOT_QUEUE_REMOVED = "⚠️ You were taken out of the line.\n\nJoin again — /queue"

BOT_OFFER = (
    "🔔 <b>{machine}</b> ({room}) is free and it's your turn.\n"
    "Book it before <b>{time}</b> — after that it goes to the next person in line.\n\n"
    "Book it from the tablet in the room or in the app (/book)."
)
BOT_OFFER_EXPIRED = (
    "⌛️ Your time on {machine} ran out — it went to the next person in line.\n\n"
    "Join the line again — /queue"
)
BOT_OFFER_NIGHT_HINT = (
    "🌙 The clock stops overnight — your time picks up again in the morning."
)

# Used if the machine name somehow could not be found.
BOT_MACHINE_FALLBACK = "the machine"


# --- bot: machines -----------------------------------------------------------

BOT_OCCUPIED = (
    "🔴 You've booked <b>{machine}</b> for about {left} (until {time}).\n\n"
    "I'll remind you 15 minutes before it ends."
)
BOT_RELEASED = "🟢 <b>{machine}</b> is free again."

# No gender in the Russian original for a reason; keep it plain here too — the
# names are real and this line gets read every day.
BOT_RELEASED_BY_OTHER = (
    "⚠️ Your job on <b>{machine}</b> was stopped ({name}) — it's free again.\n\n"
    "If you still need the part, it's waiting at the machine."
)

# The first line is the same for every kind; what to do next depends on whether
# it's a printer or a meeting room (`MACHINE_*_HINT` above). The hint always
# comes after a blank line: it is the "what to do", the part people look for.
BOT_ALMOST_DONE = "⏳ <b>{machine}</b>: your time is nearly up, about {left} left.\n\n{hint}"
BOT_FINISHED = "🏁 <b>{machine}</b>: your time is up.\n\n{hint}"
BOT_CHECK_MACHINE = (
    "👀 The time on <b>{machine}</b> should be over by now ({name}).\n\n{hint}"
)
BOT_UNCLAIMED_OWNER = (
    "⚠️ <b>{machine}</b> has been yours for {ago} past the end of the job.\n\n{hint}"
)
BOT_UNCLAIMED_QUEUE = "👀 <b>{machine}</b> hasn't been freed for {ago} ({name}).\n\n{hint}"

BOT_CANCELLED_BY_ADMIN = "⛔️ Your job on <b>{machine}</b> was cancelled."
BOT_CANCELLED_REASON = "\nReason: {reason}"
BOT_CANCELLED_TAIL = "\n\nIf you still need the part, it's waiting at the machine."

BOT_NOTHING_TO_FREE = (
    "👌 You haven't got a machine booked.\n\nSee the machines — /status"
)

# Freeing up from the bot works, but only while a single thing is taken: a
# person can hold one machine per room (rule 2), and guessing which one they
# meant is not on — the wrong machine would go to the line with the part still
# on the bed.
BOT_FREE_PICK = (
    "🔴 You've got more than one thing taken — free the right one in the app "
    "(/book) or on the tablet:\n\n{options}"
)


# --- bot: bookings ahead -----------------------------------------------------

BOT_BOOKED = (
    "📅 Booked: <b>{machine}</b> ({room})\n"
    "{start} — {end}\n\n"
    "I'll remind you an hour before. Cancel it — /my"
)

BOT_BOOKING_SOON = (
    "⏰ Your booking starts in {left}: <b>{machine}</b> at {time}.\n\n"
    "Come over and take it from the tablet or in the app."
)

BOT_BOOKING_AFTER_YOU = (
    "⏰ Someone has <b>{machine}</b> booked at {time}, right after you.\n\n"
    "Please free it up before then so they don't arrive to a busy table."
)

BOT_BOOKING_STARTED = (
    "🔔 Your slot on <b>{machine}</b> has started and it's free right now.\n\n"
    "Take it before {time} or the booking is dropped."
)

BOT_BOOKING_STARTED_BUSY = (
    "⏳ Your slot on <b>{machine}</b> has started, but it hasn't been freed yet.\n\n"
    "You can free it yourself — say so on the tablet and take it.\n"
    "While it's busy your booking is not dropped."
)

BOT_BOOKING_MISSED = (
    "⌛️ Your booking on <b>{machine}</b> is dropped: it sat free and you "
    "didn't take it within {minutes} min.\n\n"
    "Book again — /book"
)

BOT_BOOKING_CANCELLED = "✅ The booking on <b>{machine}</b> ({start}) is cancelled."

BOT_BOOKING_CANCELLED_BY_ADMIN = (
    "⛔️ Your booking on <b>{machine}</b> ({start}) was cancelled."
)

BOT_MY_BOOKING = "📅 Booking: <b>{machine}</b> ({room})\n{start} — {end}"

BOT_BOOK_INVITE = "📅 The schedule and your bookings live in the app:"
BOT_BOOK_BUTTON = "Open the schedule"
BOT_BOOK_NO_APP = (
    "⚠️ The app isn't set up: the server has no https address, so Telegram won't "
    "open a mini app. Book from the tablet in the room instead."
)


# --- refusals: rooms ---------------------------------------------------------

ERR_ROOM_NOT_FOUND = "No such room"
ERR_ROOM_NAME_EMPTY = "Give the room a name"
ERR_ROOM_NAME_LONG = "A name longer than {limit} characters won't fit"
ERR_ROOM_NAME_TAKEN = "The name {name} already belongs to another room"
ERR_ROOM_NAME_TAKEN_BY_MACHINE = (
    "The name {name} already belongs to a machine — a meeting room and its "
    "bookable self share one name, so pick another"
)
ERR_ROOM_KIND_UNKNOWN = "Unknown kind of room: {kind}"
ERR_ROOM_NOT_EMPTY = (
    "{room} can't be deleted: it still holds {machines} machine(s) and {queue} "
    "wait(s) in the log. Take the machines out first — and if the room simply "
    "closed, leave it empty: that keeps the log intact."
)
# A meeting room has no machines to take out — it is bookable as itself, so only
# its own history stands in the way.
ERR_ROOM_HAS_HISTORY = (
    "{room} can't be deleted: {history} record(s) in the log point at it — jobs, "
    "offers and bookings. If the room closed, take it out of service on the "
    "Summary section: nobody can book it, and the log stays intact."
)
ERR_MACHINE_KIND_NOT_IN_ROOM = "You can't put {kind} in “{room}”"
ERR_NO_ROOMS = "No rooms yet — add them in the admin panel"


# --- refusals: machines and the line -----------------------------------------

ERR_MACHINE_NOT_FOUND = "No such machine"
ERR_MACHINE_BROKEN = "{machine} is out of service"
ERR_MACHINE_BUSY = "{machine} is already taken"
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
ERR_ALREADY_BOOKED = (
    "You already have a booking in this room. Cancel it to book another slot"
)
ERR_RESERVATION_NOT_FOUND = "No such booking, or it is already closed"
ERR_RESERVATION_FORBIDDEN = "Only an admin can cancel someone else's booking"
ERR_RESERVATION_WINDOW_OPEN = "The booking slot hasn't run out yet"
ERR_RESERVATION_MACHINE_BUSY = "{machine} is busy — the booking waits until it frees up"
ERR_RESERVATION_WORK_HOURS = "A booking can only start during opening hours: {hours}"

ERR_WORK_HOURS_ORDER = "Closing time must be later than opening (00:00 — round the clock)"
ERR_WORK_HOURS_FORMAT = "“{value}” doesn't look like a time — try something like 08:00"

# The limits are counted per room (rules 2 and 13): a printer you took doesn't
# stop you booking the meeting room, and the refusal says so — otherwise it
# reads as "the system is broken".
ERR_USER_BUSY = "You've already got something in this room"
ERR_USER_BUSY_FREE_FIRST = "You've already got something in this room — free it up first"
ERR_ALREADY_IN_QUEUE = "You're already in this room's line"
ERR_NOT_IN_QUEUE = "You're not in this room's line"
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
    "{machine} can't be deleted: the log has {sessions} job(s), {offers} offer(s) "
    "and {bookings} booking(s) behind it. If the machine is gone for good, take "
    "it out of service instead — that keeps the log intact."
)


# --- refusals: access --------------------------------------------------------

ERR_KIOSK_ONLY = (
    "Machines can be taken from the tablet in the room or in the bot's app (/book)"
)
ERR_KIOSK_ROOM_UNKNOWN = (
    "This tablet isn't tied to a room — set it up again at /kiosk/enroll and pick one"
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
ERR_LAST_ADMIN = (
    "That's the last admin — deleting them isn't allowed: the panel writes every "
    "action to the log on their behalf, and without them the admin locks itself "
    "out. Give someone else the rights first: python -m app.cli make_admin <tg_id>"
)
ERR_CHAT_ID_FORMAT = "A Telegram chat id is a positive whole number"
ERR_CHAT_ID_TAKEN = "Telegram {chat_id} already belongs to somebody else"
ERR_LOGIN_FORMAT = "A login looks like n_username in Latin letters, e.g. i_ivanov"
ERR_LOGIN_TAKEN = "The login {login} already belongs to someone else"


# --- kiosk: banners after an action ------------------------------------------

FLASH_KIOSK = {
    "occupied": "Taken — have a good one!",
    "released": "Freed up",
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
    "room_added": "Room added",
    "room_renamed": "Room renamed",
    "room_removed": "Room deleted",
    "person_added": "Person added",
    "person_removed": "Person deleted",
    "machine_purged": "The machine is gone, history and all",
    "room_purged": "The room is gone with everything in it",
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

# The heading and hint of the "got your part / room is empty" screen depend on
# the kind: see MACHINE_DONE_CONFIRM and MACHINE_DONE_HINT.
CONFIRM_RELEASE_TITLE = "Free it up?"
CONFIRM_RELEASE_HINT = "The job will be stopped and marked as cancelled."
CONFIRM_RELEASE_SUBMIT = "Yes, free it up"

CONFIRM_QUEUE_JOIN_TITLE = "Join the line?"
CONFIRM_QUEUE_JOIN_HINT = (
    "When a machine frees up, the first person in line gets a message on Telegram "
    "and 30 minutes to claim it."
)
CONFIRM_QUEUE_JOIN_SUBJECT = "Line for {title} — {room}"
CONFIRM_QUEUE_JOIN_SUBMIT = "Join the line"

CONFIRM_QUEUE_LEAVE_TITLE = "Leave the line?"
CONFIRM_QUEUE_LEAVE_HINT = "You'll lose your place — joining again puts you at the back."
CONFIRM_QUEUE_LEAVE_SUBJECT = "The line — {room}"
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

LOG_QUEUE_JOINED = "Joined the line for {kind} ({room}): {name}"
LOG_QUEUE_OFFERED = "{machine} offered to {name}"
LOG_QUEUE_RESOLVED = "{word}: {name}"
LOG_QUEUE_TAKEN = "Offer taken"
LOG_QUEUE_EXPIRED = "Time ran out"
LOG_QUEUE_LEFT = "Left the line"


# --- other -------------------------------------------------------------------

API_TITLE = "Machine and meeting room booking"


# --- HTML templates ----------------------------------------------------------
#
# Available in Jinja as `t.<key>`, registered in app/api/render.py.

UI = {
    # base.html
    "lang": LANG,
    "app_title": "Coworking",
    "app_short_title": "Coworking",
    "offline_banner": "No connection to the server. What's on screen may be out of date.",
    # _board.html
    "board_occupy_cta": "Book {kind}",
    "board_free_of": "{free} of {total} free",
    "board_queue_cta": "Join the line",
    "board_all_busy": "all busy",
    "board_park_empty": "Nothing here yet — add machines in the admin panel",
    "tile_broken": "Out of service",
    "tile_busy": "Busy",
    "tile_until": "until {time}",
    "tile_release": "Free up",
    "tile_done_at": "finished at {time}",
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
    # _keypad.html
    "keypad_label": "PIN",
    "keypad_clear": "Clear",
    # The line under the keypad stays for deployments that left the bot's
    # username out of .env — there's no QR to show then (see app/qr.py).
    "keypad_hint": "Send /start to the bot to get a PIN",
    "pin_help_open": "How to get a PIN",
    "pin_help_title": "The bot hands out PINs",
    "pin_help_body": (
        "Point your phone's camera at the code and send /start to the bot — "
        "it asks for your corporate login and replies with a PIN."
    ),
    "pin_help_close": "Got it",
    # confirm.html / occupy.html
    "cancel": "Cancel",
    # On a screen where nothing has started, "Cancel" lies — there's nothing to cancel.
    "back": "Back",
    "occupy_title": "Book {machine}",
    "occupy_heading": "{machine} — book it now",
    "occupy_duration_label": "For how long",
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
    "schedule_empty": "Nothing of this kind in this room yet",
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
    "my_where": "{machine} · {room}",
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
    # admin_base.html — the menu on the left
    "admin_nav_now": "Right now",
    "admin_nav_setup": "Setup",
    "admin_nav_screens": "Screens",
    "admin_nav_people": "People",
    "admin_nav_events": "Activity log",
    "admin_side_note": "Access by ADMIN_SECRET. Closed from outside, SSH tunnel only.",
    # admin.html
    "admin_title": "Admin",
    "admin_to_board": "Back to the rooms",
    "admin_updated": "As of {time}",
    "admin_stat_free": "Free",
    "admin_stat_busy": "In use",
    "admin_stat_broken": "Out of service",
    "admin_stat_queue": "Waiting in line",
    "admin_stat_bookings": "Bookings ahead",
    "admin_stat_of": "of {count}",
    "admin_stat_people": "people: {count}",
    "admin_stat_ahead": "upcoming",
    "admin_tab_summary": "Summary",
    "admin_tab_rooms": "Rooms",
    "admin_tab_machines": "Machines",
    "admin_tab_hours": "Opening hours",
    "admin_bookings": "Bookings",
    "admin_bookings_none": "No bookings",
    "admin_booking_row": "{machine}: {start} — {end}",
    "admin_booking_where": "{room}",
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
    "admin_people_none": "Nobody has registered with the bot yet",
    # admin/confirm_delete.html — the shared delete confirmation screen
    "admin_delete_confirm": "Delete “{name}”?",
    "admin_delete_what": "This goes with it:",
    "admin_delete_machines": "machines: {count}",
    "admin_delete_sessions": "jobs in the log: {count}",
    "admin_delete_bookings": "bookings: {count}",
    "admin_delete_queue": "line entries: {count}",
    "admin_delete_nothing": "Nothing is recorded against it — deleting touches nothing else.",
    "admin_delete_warning": "These records leave the log for good. Deleting can't be undone.",
    "admin_delete_instead_machine": (
        "If the machine simply left but you want its history, take it out of "
        "service on the Summary section — it stays in the log but can't be booked."
    ),
    "admin_delete_submit": "Delete for good",
    "admin_delete_cancel": "Cancel",
    # the People section
    "admin_people_add": "Add a person",
    "admin_people_login_placeholder": "login, e.g. i_ivanov",
    "admin_people_chat_label": "Telegram chat id",
    "admin_people_chat_placeholder": "e.g. 900001",
    "admin_people_pin_label": "PIN",
    "admin_people_pin_placeholder": "four digits",
    "admin_people_add_submit": "Add",
    "admin_people_delete": "Delete",
    "admin_people_hint": (
        "Normally people add themselves — /start with the bot, and the chat id "
        "comes from Telegram. Here you type it in: that's for test accounts, "
        "which have no Telegram at all, and for somebody who never reached the "
        "bot. The PIN is typed rather than generated: a generated one would have "
        "to be shown on screen, and from there it goes into browser history and "
        "over your shoulder. Read out the one you typed — they can change it "
        "with the bot."
    ),
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
    "app_nav_board": "Rooms",
    "app_nav_my": "My bookings",
    # admin_rooms.html
    "admin_rooms_title": "Rooms",
    "admin_rooms_add": "Add a room",
    "admin_rooms_name_placeholder": "name, e.g. Meeting room “Oak”",
    "admin_rooms_kind_label": "Kind",
    "admin_rooms_add_submit": "Add",
    "admin_rooms_none": "No rooms yet",
    "admin_rooms_delete": "Delete",
    "admin_rooms_machines": "machines: {count}",
    "admin_rooms_queue": "waits in the log: {count}",
    "admin_rooms_history": "records in the log: {count}",
    "admin_rooms_open": "Open the screen",
    "admin_rooms_empty": "empty — safe to delete",
    "admin_rooms_hours": "Hours: {hours}",
    "admin_rooms_kiosk": "Tablet address: {url}",
    "admin_rooms_hint": (
        "A room is where the rules are drawn: it has its own line, its own "
        "opening hours and its own \"one job and one booking per person\" limit. "
        "The kind is fixed once created: jobs and bookings already point at this "
        "room. A meeting room is bookable as itself — that entry is created with "
        "the room and renamed together with it. "
        "A workshop can be deleted once no machines are left in it; a meeting "
        "room goes away together with its bookable self. Only history stands in "
        "the way: jobs, offers and bookings. A closed room that has history is "
        "best left as is, with its machines taken out of service — that keeps "
        "the log intact."
    ),
    # admin_machines.html
    "admin_machines_title": "Machines",
    "admin_machines_add": "Add a machine",
    "admin_machines_name_placeholder": "name, e.g. P2S #3",
    "admin_machines_kind_label": "Kind",
    "admin_machines_room_label": "Room",
    "admin_machines_add_submit": "Add",
    "admin_machines_no_rooms": "Add a room first — a machine needs somewhere to stand",
    "admin_machines_none": "No machines of this kind",
    "admin_machines_delete": "Delete",
    "admin_machines_history": (
        "in the log: {sessions} job(s), {offers} offer(s), {bookings} booking(s)"
    ),
    "admin_machines_no_history": "no history — safe to delete",
    "admin_machines_hint": (
        "The name shows up on the room's screen and in every bot message. "
        "The kind and the room are fixed once created: the log already points at "
        "this machine, and jobs and bookings remember where they happened. "
        "Don't delete a machine that's gone for good — take it out of service on "
        "the Summary section instead, so it stays in the log but can't be booked."
    ),
    # admin_hours.html
    "admin_hours_title": "Opening hours",
    "admin_hours_block": "When {room} is open",
    "admin_hours_opens": "Opens",
    "admin_hours_closes": "Closes",
    "admin_hours_submit": "Save",
    "admin_hours_current": "Right now: {hours}",
    "admin_hours_none": "Add a room first — opening hours belong to a room",
    "admin_hours_hint": (
        "Every room has its own hours. A booking can only start during them — "
        "the schedule shows them and nothing else. When it ends is not limited: "
        "a print started at 19:00 runs through the night, the part gets picked "
        "up after opening. For a round-the-clock room set both to 00:00. "
        "Existing bookings are left alone: if you moved the hours, check the "
        "list of bookings in the Bookings section."
    ),
    # kiosk_enroll.html — tying a tablet to a room
    "enroll_title": "Which room is this tablet in?",
    "enroll_heading": "Which room is this tablet in?",
    "enroll_hint": (
        "A tablet hangs in one room and shows only that room. "
        "Pick it — the choice is remembered in the tablet itself."
    ),
    "enroll_none": "No rooms yet — add them in the admin panel",
    "enroll_current": "This tablet is tied to “{room}” right now",
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
