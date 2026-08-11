"""Сборка сообщений бота.

Своих формулировок здесь нет: все строки лежат в `app/texts.py`, а этот модуль
только подставляет в них имена, время и числа. Разделение нужно, чтобы правка
текста не требовала читать логику, а перевод не требовал её трогать вовсе.

Сообщения читают люди, которые в этот момент чаще всего идут по коридору к
принтеру, поэтому в каждом сразу видно: какой принтер, что произошло и что
делать. Отсюда же тексты берёт планировщик на шаге 7.
"""

from datetime import datetime

from app import texts as t
from app.config import settings
from app.enums import PrinterStatus
from app.services.board import Board

# Планировщик и старые импорты ждут эти имена здесь.
HELP = t.BOT_HELP
STATUS_MARKS = t.BOT_STATUS_MARKS
STATUS_WORDS = t.BOT_STATUS_WORDS


def hhmm(value: datetime | None) -> str:
    return value.astimezone(settings.zone).strftime(t.TIME_FORMAT) if value else ""


def humanize(minutes: int) -> str:
    minutes = abs(int(minutes))
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return t.UNIT_HOURS_MINUTES.format(hours=hours, minutes=rest)
    if hours:
        return t.UNIT_HOURS.format(hours=hours)
    return t.UNIT_MINUTES.format(minutes=rest)


def left_until(moment: datetime, now: datetime) -> str:
    # Округляем, а не отбрасываем: иначе сразу после занятия на два часа бот
    # напишет «осталось ~1 ч 59 мин», и человек решит, что что-то не так.
    return humanize(round((moment - now).total_seconds() / 60))


# --- регистрация -------------------------------------------------------------


def ask_login() -> str:
    return t.BOT_ASK_LOGIN


def bad_login() -> str:
    return t.BOT_BAD_LOGIN


def login_taken(login: str) -> str:
    return t.BOT_LOGIN_TAKEN.format(login=login)


def welcome(login: str, pin: str) -> str:
    return t.BOT_WELCOME.format(login=login, pin=pin, help=t.BOT_HELP)


def already_registered(name: str) -> str:
    return t.BOT_ALREADY_REGISTERED.format(name=name, help=t.BOT_HELP)


def pin_changed(pin: str) -> str:
    return t.BOT_PIN_CHANGED.format(pin=pin)


def name_changed(previous: str, login: str) -> str:
    return t.BOT_NAME_CHANGED.format(previous=previous, login=login)


# --- статус ------------------------------------------------------------------


def status(board: Board) -> str:
    lines = []
    for printer in board.printers:
        mark = t.BOT_STATUS_MARKS.get(printer.status, t.BOT_STATUS_MARK_UNKNOWN)
        word = t.BOT_STATUS_WORDS.get(printer.status, printer.status)

        if printer.reserved_for and printer.status == PrinterStatus.FREE:
            lines.append(
                t.BOT_STATUS_RESERVED.format(
                    mark=mark,
                    printer=printer.name,
                    name=printer.reserved_for,
                    time=hhmm(printer.reserved_until),
                )
            )
            continue

        lines.append(t.BOT_STATUS_LINE.format(mark=mark, printer=printer.name, word=word))
        if printer.status == PrinterStatus.PRINTING and printer.eta_at:
            lines.append(
                t.BOT_STATUS_PRINTING.format(
                    name=printer.owner_name, left=left_until(printer.eta_at, board.now)
                )
            )
        elif printer.status == PrinterStatus.DONE_WAIT and printer.done_since:
            lines.append(
                t.BOT_STATUS_DONE.format(
                    name=printer.owner_name, ago=left_until(board.now, printer.done_since)
                )
            )
        elif printer.status == PrinterStatus.BROKEN and printer.note:
            lines.append(t.BOT_STATUS_NOTE.format(note=printer.note))

    if board.queue:
        people = ", ".join(
            t.BOT_STATUS_QUEUE_PERSON.format(position=person.position, name=person.name)
            + (t.BOT_STATUS_QUEUE_OFFERED if person.offered else "")
            for person in board.queue
        )
        lines.append("\n" + t.BOT_STATUS_QUEUE.format(people=people))
    else:
        lines.append("\n" + t.BOT_STATUS_QUEUE_EMPTY)

    return "\n".join(lines)


def my_state(printer_name: str | None, eta_at: datetime | None, now: datetime,
             position: int | None, offered_printer: str | None,
             offer_until: datetime | None) -> str:
    parts = []
    if printer_name and eta_at:
        parts.append(
            t.BOT_MY_PRINTING.format(printer=printer_name, left=left_until(eta_at, now))
        )
    if offered_printer:
        parts.append(
            t.BOT_MY_OFFERED.format(printer=offered_printer, time=hhmm(offer_until))
        )
    elif position:
        parts.append(t.BOT_MY_IN_QUEUE.format(position=position))

    if not parts:
        parts.append(t.BOT_MY_NOTHING)
    return "\n\n".join(parts)


# --- очередь -----------------------------------------------------------------


def queue_joined(position: int) -> str:
    return t.BOT_QUEUE_JOINED.format(position=position)


def queue_already(position: int) -> str:
    return t.BOT_QUEUE_ALREADY.format(position=position)


def queue_left() -> str:
    return t.BOT_QUEUE_LEFT


def offer(printer_name: str, expires_at: datetime) -> str:
    return t.BOT_OFFER.format(printer=printer_name, time=hhmm(expires_at))


def offer_expired(printer_name: str) -> str:
    return t.BOT_OFFER_EXPIRED.format(printer=printer_name)


def offer_night_hint() -> str:
    return t.BOT_OFFER_NIGHT_HINT


# --- принтеры ----------------------------------------------------------------


def occupied(printer_name: str, eta_at: datetime, now: datetime) -> str:
    return t.BOT_OCCUPIED.format(
        printer=printer_name, left=left_until(eta_at, now), time=hhmm(eta_at)
    )


def released(printer_name: str) -> str:
    return t.BOT_RELEASED.format(printer=printer_name)


def released_by_other(printer_name: str, actor_name: str) -> str:
    return t.BOT_RELEASED_BY_OTHER.format(printer=printer_name, name=actor_name)


def almost_done(printer_name: str, minutes: int) -> str:
    return t.BOT_ALMOST_DONE.format(printer=printer_name, left=humanize(minutes))


def finished(printer_name: str) -> str:
    return t.BOT_FINISHED.format(printer=printer_name)


def check_printer(printer_name: str, owner_name: str) -> str:
    return t.BOT_CHECK_PRINTER.format(printer=printer_name, name=owner_name)


def unclaimed_owner(printer_name: str, minutes: int) -> str:
    return t.BOT_UNCLAIMED_OWNER.format(printer=printer_name, ago=humanize(minutes))


def unclaimed_queue(printer_name: str, owner_name: str, minutes: int) -> str:
    return t.BOT_UNCLAIMED_QUEUE.format(
        printer=printer_name, name=owner_name, ago=humanize(minutes)
    )


def print_cancelled_by_admin(printer_name: str, reason: str | None) -> str:
    text = t.BOT_CANCELLED_BY_ADMIN.format(printer=printer_name)
    if reason:
        text += t.BOT_CANCELLED_REASON.format(reason=reason)
    return text + t.BOT_CANCELLED_TAIL


def removed_from_queue() -> str:
    return t.BOT_QUEUE_REMOVED


def nothing_to_free() -> str:
    return t.BOT_NOTHING_TO_FREE


def not_registered() -> str:
    return t.BOT_NOT_REGISTERED
