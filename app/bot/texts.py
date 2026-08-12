"""Сборка сообщений бота.

Своих формулировок здесь нет: все строки лежат в `app/texts.py`, а этот модуль
только подставляет в них имена, время и числа. Разделение нужно, чтобы правка
текста не требовала читать логику, а перевод не требовал её трогать вовсе.

Сообщения читают люди, которые в этот момент чаще всего идут по коридору к
машине, поэтому в каждом сразу видно: какая машина, что произошло и что
делать. Отсюда же тексты берёт планировщик на шаге 7.
"""

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


def kind_word(kind: str) -> str:
    """«принтер» / «гравировщик» — для фраз про очередь."""
    return t.MACHINE_KIND_ONE.get(kind, kind)


def busy_word(kind: str) -> str:
    """Что машина делает, пока занята: печатает или гравирует."""
    return t.MACHINE_BUSY_WORD.get(kind, t.MACHINE_KIND_ONE.get(kind, kind))


def status(board: Board) -> str:
    """Состояние парка секциями по типам — как на экране в мастерской."""
    if not board.groups:
        return t.BOT_STATUS_PARK_EMPTY

    blocks = []
    for group in board.groups:
        title = t.MACHINE_KIND_TITLE.get(group.kind, group.kind)
        lines = [t.BOT_STATUS_SECTION.format(title=title)]
        lines.extend(_machine_lines(group, board))
        lines.append(_queue_line(group))
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _machine_lines(group, board: Board) -> list[str]:
    lines = []
    for machine in group.machines:
        mark = t.BOT_STATUS_MARKS.get(machine.status, t.BOT_STATUS_MARK_UNKNOWN)

        if machine.reserved_for and machine.status == MachineStatus.FREE:
            lines.append(
                t.BOT_STATUS_RESERVED.format(
                    mark=mark,
                    machine=machine.name,
                    name=machine.reserved_for,
                    time=hhmm(machine.reserved_until),
                )
            )
            continue

        if machine.status == MachineStatus.PRINTING:
            word = busy_word(machine.kind)
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


def _queue_line(group) -> str:
    if not group.queue:
        return t.BOT_STATUS_QUEUE_EMPTY
    people = ", ".join(
        t.BOT_STATUS_QUEUE_PERSON.format(position=person.position, name=person.name)
        + (t.BOT_STATUS_QUEUE_OFFERED if person.offered else "")
        for person in group.queue
    )
    return t.BOT_STATUS_QUEUE.format(people=people)


def my_state(machine_name: str | None, eta_at: datetime | None, now: datetime,
             position: int | None, queue_kind: str | None, offered_machine: str | None,
             offer_until: datetime | None) -> str:
    parts = []
    if machine_name and eta_at:
        parts.append(
            t.BOT_MY_BUSY.format(machine=machine_name, left=left_until(eta_at, now))
        )
    if offered_machine:
        parts.append(
            t.BOT_MY_OFFERED.format(machine=offered_machine, time=hhmm(offer_until))
        )
    elif position and queue_kind:
        parts.append(
            t.BOT_MY_IN_QUEUE.format(position=position, kind=kind_word(queue_kind))
        )

    if not parts:
        parts.append(t.BOT_MY_NOTHING)
    return "\n\n".join(parts)


# --- очередь -----------------------------------------------------------------


def park_empty() -> str:
    return t.BOT_STATUS_PARK_EMPTY


def queue_pick_kind(kinds: list[str]) -> str:
    """Очередей несколько — какую именно, спрашиваем командами по типу."""
    options = "\n".join(
        t.BOT_QUEUE_PICK_OPTION.format(
            command=f"/queue_{kind}", title=t.MACHINE_KIND_TITLE.get(kind, kind)
        )
        for kind in kinds
    )
    return t.BOT_QUEUE_PICK.format(options=options)


def queue_joined(position: int, kind: str) -> str:
    return t.BOT_QUEUE_JOINED.format(position=position, kind=kind_word(kind))


def queue_already(position: int) -> str:
    return t.BOT_QUEUE_ALREADY.format(position=position)


def queue_left() -> str:
    return t.BOT_QUEUE_LEFT


def offer(machine_name: str, expires_at: datetime) -> str:
    return t.BOT_OFFER.format(machine=machine_name, time=hhmm(expires_at))


def offer_expired(machine_name: str) -> str:
    return t.BOT_OFFER_EXPIRED.format(machine=machine_name)


def offer_night_hint() -> str:
    return t.BOT_OFFER_NIGHT_HINT


# --- машины ------------------------------------------------------------------


def occupied(machine_name: str, eta_at: datetime, now: datetime) -> str:
    return t.BOT_OCCUPIED.format(
        machine=machine_name, left=left_until(eta_at, now), time=hhmm(eta_at)
    )


def released(machine_name: str) -> str:
    return t.BOT_RELEASED.format(machine=machine_name)


def released_by_other(machine_name: str, actor_name: str) -> str:
    return t.BOT_RELEASED_BY_OTHER.format(machine=machine_name, name=actor_name)


def almost_done(machine_name: str, minutes: int) -> str:
    return t.BOT_ALMOST_DONE.format(machine=machine_name, left=humanize(minutes))


def finished(machine_name: str) -> str:
    return t.BOT_FINISHED.format(machine=machine_name)


def check_machine(machine_name: str, owner_name: str) -> str:
    return t.BOT_CHECK_MACHINE.format(machine=machine_name, name=owner_name)


def unclaimed_owner(machine_name: str, minutes: int) -> str:
    return t.BOT_UNCLAIMED_OWNER.format(machine=machine_name, ago=humanize(minutes))


def unclaimed_queue(machine_name: str, owner_name: str, minutes: int) -> str:
    return t.BOT_UNCLAIMED_QUEUE.format(
        machine=machine_name, name=owner_name, ago=humanize(minutes)
    )


def work_cancelled_by_admin(machine_name: str, reason: str | None) -> str:
    text = t.BOT_CANCELLED_BY_ADMIN.format(machine=machine_name)
    if reason:
        text += t.BOT_CANCELLED_REASON.format(reason=reason)
    return text + t.BOT_CANCELLED_TAIL


def removed_from_queue() -> str:
    return t.BOT_QUEUE_REMOVED


def nothing_to_free() -> str:
    return t.BOT_NOTHING_TO_FREE


def not_registered() -> str:
    return t.BOT_NOT_REGISTERED
