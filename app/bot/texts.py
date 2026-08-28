"""Сборка сообщений бота.

Своих формулировок здесь нет: все строки лежат в `app/texts.py`, а этот модуль
только подставляет в них имена, время и числа. Разделение нужно, чтобы правка
текста не требовала читать логику, а перевод не требовал её трогать вовсе.

Сообщения читают люди, которые в этот момент чаще всего идут по коридору к
машине, поэтому в каждом сразу видно: какая машина, что произошло и что
делать. Отсюда же тексты берёт планировщик на шаге 7.
"""

from dataclasses import dataclass
from datetime import datetime

from app import texts as t
from app.config import settings
from app.enums import MachineStatus
from app.services.board import Board

# Планировщик и старые импорты ждут эти имена здесь.
HELP = t.BOT_HELP
STATUS_MARKS = t.BOT_STATUS_MARKS
STATUS_WORDS = t.BOT_STATUS_WORDS


def hhmm(value: datetime | None) -> str:
    return value.astimezone(settings.zone).strftime(t.TIME_FORMAT) if value else ""


def when(value: datetime | None) -> str:
    """Дата и время: у брони на будущее одного часа мало — «в 14:00» какого дня?"""
    return value.astimezone(settings.zone).strftime(t.DATETIME_FORMAT) if value else ""


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


def busy_word(kind: str) -> str:
    """Что машина делает, пока занята: печатает или гравирует."""
    return t.MACHINE_BUSY_WORD.get(kind, t.MACHINE_KIND_ONE.get(kind, kind))


def done_word(kind: str) -> str:
    """Что значит «работа кончилась, но не свободно» — у принтера и у комнаты."""
    return t.MACHINE_DONE_WORD.get(kind, MachineStatus.DONE_WAIT)


def status(board: Board) -> str:
    """Состояние парка блоками по помещениям — как на экранах в них самих.

    Внутри помещения — секции по типам, но только если типов больше одного: в
    переговорной подзаголовок «Переговорная» повторял бы имя комнаты.

    Пустая строка стоит между всеми частями — и между помещениями, и между
    секциями внутри них. Разделителя двух уровней у Telegram нет, поэтому
    границу помещения держит не отбивка, а сама строка заголовка: значок типа и
    жирное имя видны среди светлых курсивных подзаголовков.
    """
    if not board.rooms:
        return t.BOT_STATUS_PARK_EMPTY

    blocks = []
    for room in board.rooms:
        parts = [
            t.BOT_STATUS_ROOM.format(
                mark=t.ROOM_KIND_MARK.get(room.kind, ""), name=room.name
            )
        ]
        for group in room.groups:
            lines = []
            if not room.single_group:
                title = t.MACHINE_KIND_TITLE.get(group.kind, group.kind)
                lines.append(t.BOT_STATUS_SECTION.format(title=title))
            lines.extend(_machine_lines(group, board))
            parts.append("\n".join(lines))
        blocks.append("\n\n".join(parts))

    return "\n\n".join(blocks)


def _machine_lines(group, board: Board) -> list[str]:
    lines = []
    for machine in group.machines:
        mark = t.BOT_STATUS_MARKS.get(machine.status, t.BOT_STATUS_MARK_UNKNOWN)

        if machine.status == MachineStatus.PRINTING:
            word = busy_word(machine.kind)
        elif machine.status == MachineStatus.DONE_WAIT:
            word = done_word(machine.kind)
        else:
            word = t.BOT_STATUS_WORDS.get(machine.status, machine.status)
        lines.append(t.BOT_STATUS_LINE.format(mark=mark, machine=machine.name, word=word))

        if machine.status == MachineStatus.PRINTING and machine.eta_at:
            lines.append(
                t.BOT_STATUS_BUSY.format(
                    name=machine.owner_name, left=left_until(machine.eta_at, board.now)
                )
            )
        elif machine.status == MachineStatus.DONE_WAIT and machine.done_since:
            lines.append(
                t.BOT_STATUS_DONE.format(
                    name=machine.owner_name, ago=left_until(board.now, machine.done_since)
                )
            )
        elif machine.status == MachineStatus.BROKEN and machine.note:
            lines.append(t.BOT_STATUS_NOTE.format(note=machine.note))
    return lines


@dataclass(frozen=True)
class MyWork:
    """Одна из активных работ человека."""

    machine: str
    room: str
    eta_at: datetime


@dataclass(frozen=True)
class MyBooking:
    machine: str
    room: str
    starts_at: datetime
    ends_at: datetime


def my_state(
    now: datetime,
    works: list[MyWork],
    bookings: list[MyBooking],
) -> str:
    """Всё, что за человеком числится, — списками, а не по одному.

    Списки обязательны: расширенный лимит разрешает человеку одновременно
    работать на нескольких единицах оборудования.
    """
    parts = [
        t.BOT_MY_BUSY.format(
            machine=work.machine, room=work.room, left=left_until(work.eta_at, now)
        )
        for work in works
    ]
    parts += [
        t.BOT_MY_BOOKING.format(
            machine=booking.machine,
            room=booking.room,
            start=when(booking.starts_at),
            end=hhmm(booking.ends_at),
        )
        for booking in bookings
    ]
    if not parts:
        parts.append(t.BOT_MY_NOTHING)
    return "\n\n".join(parts)


# --- брони -------------------------------------------------------------------


def booked(
    machine_name: str, room_name: str, starts_at: datetime, ends_at: datetime
) -> str:
    return t.BOT_BOOKED.format(
        machine=machine_name, room=room_name, start=when(starts_at), end=hhmm(ends_at)
    )


def booking_soon(machine_name: str, starts_at: datetime, minutes: int) -> str:
    return t.BOT_BOOKING_SOON.format(
        machine=machine_name, time=hhmm(starts_at), left=humanize(minutes)
    )


def booking_after_you(machine_name: str, starts_at: datetime) -> str:
    return t.BOT_BOOKING_AFTER_YOU.format(machine=machine_name, time=hhmm(starts_at))


def booking_started(machine_name: str, deadline: datetime) -> str:
    return t.BOT_BOOKING_STARTED.format(machine=machine_name, time=hhmm(deadline))


def booking_started_busy(machine_name: str) -> str:
    return t.BOT_BOOKING_STARTED_BUSY.format(machine=machine_name)


def booking_missed(machine_name: str, minutes: int) -> str:
    return t.BOT_BOOKING_MISSED.format(machine=machine_name, minutes=minutes)


def booking_cancelled(machine_name: str, starts_at: datetime) -> str:
    return t.BOT_BOOKING_CANCELLED.format(machine=machine_name, start=when(starts_at))


def booking_cancelled_by_admin(machine_name: str, starts_at: datetime) -> str:
    return t.BOT_BOOKING_CANCELLED_BY_ADMIN.format(
        machine=machine_name, start=when(starts_at)
    )


def book_blocked(reason: str) -> str:
    """Причина отказа приходит готовой строкой из домена — той же, что на экране."""
    return t.BOT_BOOK_BLOCKED.format(reason=reason.rstrip("."))


def book_invite() -> str:
    return t.BOT_BOOK_INVITE


def book_no_app() -> str:
    return t.BOT_BOOK_NO_APP


# --- машины ------------------------------------------------------------------


def occupied(machine_name: str, eta_at: datetime, now: datetime) -> str:
    return t.BOT_OCCUPIED.format(
        machine=machine_name, left=left_until(eta_at, now), time=hhmm(eta_at)
    )


def released(machine_name: str) -> str:
    return t.BOT_RELEASED.format(machine=machine_name)


def released_by_other(machine_name: str, actor_name: str) -> str:
    return t.BOT_RELEASED_BY_OTHER.format(machine=machine_name, name=actor_name)


# Что делать в конце работы, зависит от типа: у принтера это деталь на столе, у
# переговорной — выйти из комнаты. Отсюда `kind` в каждом из напоминаний.


def almost_done(machine_name: str, kind: str, minutes: int) -> str:
    return t.BOT_ALMOST_DONE.format(
        machine=machine_name,
        left=humanize(minutes),
        hint=t.MACHINE_ALMOST_DONE_HINT.get(kind, ""),
    )


def finished(machine_name: str, kind: str) -> str:
    return t.BOT_FINISHED.format(
        machine=machine_name, hint=t.MACHINE_FINISHED_HINT.get(kind, "")
    )


def unclaimed_owner(machine_name: str, kind: str, minutes: int) -> str:
    return t.BOT_UNCLAIMED_OWNER.format(
        machine=machine_name,
        ago=humanize(minutes),
        hint=t.MACHINE_UNCLAIMED_OWNER_HINT.get(kind, ""),
    )


def work_cancelled_by_admin(machine_name: str, reason: str | None) -> str:
    text = t.BOT_CANCELLED_BY_ADMIN.format(machine=machine_name)
    if reason:
        text += t.BOT_CANCELLED_REASON.format(reason=reason)
    return text + t.BOT_CANCELLED_TAIL


def nothing_to_free() -> str:
    return t.BOT_NOTHING_TO_FREE


def free_pick(options: list[tuple[str, str]]) -> str:
    """Занято сразу в нескольких помещениях — какую машину освободить, решает он."""
    lines = "\n".join(
        t.BOT_QUEUE_PICK_OPTION.format(room=room, title=machine)
        for room, machine in options
    )
    return t.BOT_FREE_PICK.format(options=lines)


def not_registered() -> str:
    return t.BOT_NOT_REGISTERED
