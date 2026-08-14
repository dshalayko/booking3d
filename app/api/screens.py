"""Экраны и действия — один набор на киоск и на Mini App.

Планшет на стене и телефон в Telegram показывают одно и то же: список помещений,
доску помещения, расписание, формы занятия и брони. Отличаются они ровно двумя
вещами, и обе описаны в `Client`: префикс адресов и нужен ли PIN. Всё остальное —
этот модуль.

Помещение есть в адресе почти каждого экрана: доска, расписание и очередь у
каждого свои. Планшет своё помещение знает (оно записано в его device-cookie,
см. api/kiosk.py), телефон выбирает из списка.

Разделение сделано ради одной строки правил: вторая копия «показать расписание»
или «занять машину» разошлась бы с первой на первой же правке, а расходятся такие
копии молча. Роутеры (api/kiosk.py, api/miniapp.py) остались тонкими: они решают
только, кто действует — PIN на киоске, подпись Telegram в Mini App.

Функции возвращают готовый `Response`. Транзакцией управляют они же: доменный
слой не коммитит, а страница обязана отдаваться после коммита.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.api.render import hhmm, templates, when
from app.bot import notify, texts
from app.config import settings
from app.enums import MachineKind, MachineStatus
from app.models import Machine, Room, User
from app.services import board as board_svc
from app.services import machines as machines_svc
from app.services import queue as queue_svc
from app.services import reservations as reservations_svc
from app.services import rooms as rooms_svc
from app.services import schedule as schedule_svc
from app.services import workhours as workhours_svc
from app.services.errors import (
    InvalidReservationTime,
    MachineKindUnknown,
    MachineNotAvailable,
    ReservationOverlap,
    RoomNotFound,
)


@dataclass(frozen=True)
class Client:
    """Кто спрашивает: планшет на стене или Mini App в Telegram.

    `base` — префикс адресов действий, `needs_pin` — рисовать ли клавиатуру.
    Из `needs_pin` же следует авто-возврат на главный экран: он нужен потому, что
    планшет общий, а личный телефон никуда возвращать не надо.
    """

    base: str
    needs_pin: bool
    # Подключать ли telegram-web-app.js: он разворачивает окно на весь экран,
    # рисует системную кнопку «назад» и отдаёт подпись открытия. Внутри Telegram
    # это уместно, на стене — лишний внешний запрос.
    telegram_sdk: bool = False

    @property
    def context(self) -> dict:
        return {
            "base": self.base,
            "needs_pin": self.needs_pin,
            "telegram_sdk": self.telegram_sdk,
        }

    def done(self, flash: str) -> RedirectResponse:
        return RedirectResponse(
            f"{self.base}/?flash={flash}", status_code=status.HTTP_303_SEE_OTHER
        )


KIOSK = Client(base="", needs_pin=True)
APP = Client(base="/app", needs_pin=False, telegram_sdk=True)


# --- вспомогательное ---------------------------------------------------------


def valid_kind(kind: str) -> str:
    """Тип из адреса. Экран подтверждения не должен открываться на опечатке."""
    if kind not in tuple(MachineKind):
        raise MachineKindUnknown(t.ERR_MACHINE_KIND_UNKNOWN.format(kind=kind))
    return kind


def duration_options(
    start: datetime, limit_minutes: int | None = None, minimum: int | None = None
) -> list[schedule_svc.DurationOption]:
    """Кнопки длительности для занятия или брони.

    Считает services/schedule.py — та же арифметика нужна и экрану, и домену.
    """
    return schedule_svc.duration_options(
        start,
        limit_minutes=limit_minutes,
        minimum=minimum if minimum is not None else machines_svc.MIN_DURATION_MINUTES,
    )


def parse_day(value: str, now: datetime) -> date:
    """День из адреса. Мусор и даты вне горизонта сводятся к сегодняшнему.

    Расписание — экран для чтения, а не действие: отказ вместо календаря на
    опечатке в ссылке ничего не защитит, а человека у стены остановит.
    """
    today = schedule_svc.day_of(now)
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return today
    last = schedule_svc.day_of(schedule_svc.horizon_end(now))
    return min(max(day, today), last)


def parse_start(value: str) -> datetime:
    """Начало брони из адреса.

    `+` в смещении часового пояса живёт в query-строке ровно до первого клиента,
    который забудет его закодировать, и приезжает пробелом — возвращаем на место
    прежде, чем разбирать.
    """
    try:
        moment = datetime.fromisoformat((value or "").strip().replace(" ", "+"))
    except ValueError as exc:
        raise InvalidReservationTime(t.ERR_RESERVATION_PAST) from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=settings.zone)
    return moment.astimezone(UTC)


async def _machine(db: AsyncSession, machine_id: int) -> Machine:
    machine = await db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t.ERR_MACHINE_NOT_FOUND)
    return machine


async def _room(db: AsyncSession, room_id: int) -> Room:
    room = await db.get(Room, room_id)
    if room is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t.ERR_ROOM_NOT_FOUND)
    return room


# --- доска -------------------------------------------------------------------


async def default_room_id(db: AsyncSession) -> int:
    """Какое помещение показать, когда его не назвали.

    Экрана «все помещения» в системе нет: у каждой комнаты свой адрес, и попасть
    на него можно с планшета (его комната записана в метке устройства), из
    админки или по ссылке. Но корень `/` открыть можно всегда, и показать на нём
    что-то нужно — первое помещение подходит: их редко больше двух-трёх, а
    планшет со стены сюда и не заходит.
    """
    rooms = await rooms_svc.list_rooms(db)
    if not rooms:
        raise RoomNotFound(t.ERR_NO_ROOMS)
    return rooms[0].id


async def board_context(
    db: AsyncSession,
    room_id: int | None = None,
    viewer: User | None = None,
) -> dict:
    """Состояние для шаблона доски.

    С `room_id` — одно помещение целиком: так доску видят планшет на стене и
    телефон, открывший комнату. Без него — «моё»: парк сжимается до занятой
    машины и очереди её секции, по всем помещениям сразу (services/board.py,
    `personal`). Сжатый вид бывает только там, где известно, кто смотрит, то есть
    в Mini App.
    """
    if room_id is not None:
        state = await board_svc.build(db, room_id=room_id)
        rooms = state.rooms
    else:
        state = await board_svc.build(db)
        mine = None if viewer is None else board_svc.personal(state, viewer.id)
        rooms = [] if mine is None else mine.rooms

    return {
        "rooms": rooms,
        "now": state.now,
        # Сжата ли доска: от этого зависит, показывать ли большую кнопку
        # «занять» — у человека машина уже есть, а вторую он берёт из помещения.
        "focused": room_id is None,
        # Кому принадлежит кнопка «выйти из очереди»: в Mini App она действует
        # на смотрящего, и предлагать её тому, кто в этой очереди не стоит,
        # значит вести его в отказ. На киоске (None) выходят по PIN — там
        # кнопка общая, как и экран.
        "viewer_id": viewer.id if viewer is not None else None,
    }


async def board_page(
    request: Request,
    db: AsyncSession,
    client: Client,
    room_id: int,
    flash: str = "",
    viewer: User | None = None,
) -> Response:
    """Доска одного помещения — главный экран планшета на стене."""
    room = await _room(db, room_id)
    context = await board_context(db, room_id=room.id, viewer=viewer)
    context["flash"] = t.FLASH_KIOSK.get(flash)
    context["poll"] = f"{client.base}/partials/board/{room.id}"
    context.update(client.context)
    return templates.TemplateResponse(request, "kiosk.html", context)


async def board_partial(
    request: Request,
    db: AsyncSession,
    client: Client,
    room_id: int,
    viewer: User | None = None,
) -> Response:
    """Кусок страницы, который сам перезапрашивается каждые 10 секунд."""
    context = await board_context(db, room_id=room_id, viewer=viewer)
    context.update(client.context)
    return templates.TemplateResponse(request, "_board.html", context)


async def mine_page(
    request: Request,
    db: AsyncSession,
    client: Client,
    flash: str = "",
    viewer: User | None = None,
) -> Response:
    """«Моё» — доска, сжатая до занятого и своей очереди, по всем помещениям.

    Только для Mini App: на телефон заходят с вопросом «что с моей печатью», и
    чужие машины в этот момент оттесняют ответ за край экрана. Имя комнаты в
    заголовке — ссылка на её доску целиком.
    """
    context = await board_context(db, viewer=viewer)
    context["flash"] = t.FLASH_KIOSK.get(flash)
    context["poll"] = f"{client.base}/partials/board"
    context.update(client.context)
    return templates.TemplateResponse(request, "kiosk.html", context)


async def mine_partial(
    request: Request, db: AsyncSession, client: Client, viewer: User | None = None
) -> Response:
    context = await board_context(db, viewer=viewer)
    context.update(client.context)
    return templates.TemplateResponse(request, "_board.html", context)


# --- занять / освободить -----------------------------------------------------


async def occupy_page(
    request: Request, db: AsyncSession, client: Client, machine_id: int
) -> Response:
    machine = await _machine(db, machine_id)

    # Тупиковую форму показывать нельзя: человек введёт PIN, выберет время и
    # только тогда узнает, что машина занята.
    if machine.status == MachineStatus.BROKEN:
        raise MachineNotAvailable(t.ERR_MACHINE_BROKEN.format(machine=machine.name))
    if machine.status != MachineStatus.FREE:
        raise MachineNotAvailable(t.ERR_MACHINE_BUSY.format(machine=machine.name))

    now = datetime.now(UTC)
    booking = await reservations_svc.current_for_machine(db, machine.id, now)
    if booking is not None:
        # Идёт чьё-то окно. Кто перед экраном, здесь ещё неизвестно, поэтому
        # длительность ограничена концом самого окна; чужому откажет `occupy`.
        limit = int((booking.ends_at - now).total_seconds() // 60)
    else:
        limit = await reservations_svc.free_minutes(db, machine.id, now)

    return templates.TemplateResponse(
        request,
        "occupy.html",
        {
            "machine": machine,
            "durations": duration_options(now, limit_minutes=limit),
            "booked_until": booking.ends_at if booking else None,
            **client.context,
        },
    )


async def do_occupy(
    db: AsyncSession, client: Client, user: User, machine_id: int, minutes: int
) -> Response:
    result = await machines_svc.occupy(db, user, machine_id, minutes)
    await db.commit()
    await notify.send_to_user(
        db, user.id, texts.occupied(result.machine_name, result.eta_at, datetime.now(UTC))
    )
    return client.done("occupied")


async def release_page(
    request: Request, db: AsyncSession, client: Client, machine_id: int
) -> Response:
    machine = await _machine(db, machine_id)

    # Слова зависят от типа: у принтера это деталь на столе, у переговорной —
    # люди, которые вышли из комнаты.
    claimed = machine.status == MachineStatus.DONE_WAIT
    return templates.TemplateResponse(
        request,
        "confirm.html",
        {
            "title": (
                t.MACHINE_DONE_CONFIRM.get(machine.kind, t.CONFIRM_RELEASE_TITLE)
                if claimed
                else t.CONFIRM_RELEASE_TITLE
            ),
            "hint": (
                t.MACHINE_DONE_HINT.get(machine.kind, t.CONFIRM_RELEASE_HINT)
                if claimed
                else t.CONFIRM_RELEASE_HINT
            ),
            "subject": machine.name,
            "action": f"{client.base}/release/{machine.id}",
            "submit": t.CONFIRM_RELEASE_SUBMIT,
            **client.context,
        },
    )


async def do_release(
    db: AsyncSession, client: Client, user: User, machine_id: int
) -> Response:
    result = await machines_svc.release(db, user, machine_id)
    await db.commit()

    # Правило 9: снять чужую деталь можно, но владелец должен об этом узнать.
    if result.owner_user_id is not None and result.owner_user_id != user.id:
        await notify.send_to_user(
            db, result.owner_user_id, texts.released_by_other(result.machine_name, user.name)
        )
    await notify.announce_offers(db, result.offers)
    return client.done("released")


# --- очередь -----------------------------------------------------------------


async def queue_join_page(
    request: Request, db: AsyncSession, client: Client, room_id: int, kind: str
) -> Response:
    """Очередь у каждой пары (помещение, тип) своя — обе части в адресе кнопки."""
    valid_kind(kind)
    room = await _room(db, room_id)
    return templates.TemplateResponse(
        request,
        "confirm.html",
        {
            "title": t.CONFIRM_QUEUE_JOIN_TITLE,
            "hint": t.CONFIRM_QUEUE_JOIN_HINT,
            "subject": t.CONFIRM_QUEUE_JOIN_SUBJECT.format(
                title=t.MACHINE_KIND_TITLE.get(kind, kind), room=room.name
            ),
            "action": f"{client.base}/queue/join/{room.id}/{kind}",
            "submit": t.CONFIRM_QUEUE_JOIN_SUBMIT,
            **client.context,
        },
    )


async def do_queue_join(
    db: AsyncSession, client: Client, user: User, room_id: int, kind: str
) -> Response:
    result = await queue_svc.join(db, user.id, room_id, kind)
    await db.commit()
    await notify.announce_offers(db, result.offers)
    return client.done("queued")


async def queue_leave_page(
    request: Request, db: AsyncSession, client: Client, room_id: int
) -> Response:
    room = await _room(db, room_id)
    return templates.TemplateResponse(
        request,
        "confirm.html",
        {
            "title": t.CONFIRM_QUEUE_LEAVE_TITLE,
            "hint": t.CONFIRM_QUEUE_LEAVE_HINT,
            "subject": t.CONFIRM_QUEUE_LEAVE_SUBJECT.format(room=room.name),
            "action": f"{client.base}/queue/leave/{room.id}",
            "submit": t.CONFIRM_QUEUE_LEAVE_SUBMIT,
            **client.context,
        },
    )


async def do_queue_leave(
    db: AsyncSession, client: Client, user: User, room_id: int
) -> Response:
    result = await queue_svc.leave(db, user.id, room_id)
    await db.commit()
    await notify.announce_offers(db, result.offers)
    return client.done("left")


# --- расписание и брони ------------------------------------------------------


async def schedule_page(
    request: Request,
    db: AsyncSession,
    client: Client,
    room_id: int,
    kind: str,
    day_value: str = "",
    viewer: User | None = None,
) -> Response:
    """Расписание одного типа в одном помещении: часы — свои у каждой комнаты."""
    valid_kind(kind)
    room = await _room(db, room_id)
    now = datetime.now(UTC)
    park = await machines_svc.list_machines(db, room_id=room.id, kind=kind)
    grid = await reservations_svc.day_schedule(
        db,
        park,
        room,
        kind,
        parse_day(day_value, now),
        now=now,
        viewer_id=viewer.id if viewer else None,
    )
    return templates.TemplateResponse(
        request, "schedule.html", {"grid": grid, **client.context}
    )


async def book_page(
    request: Request, db: AsyncSession, client: Client, machine_id: int, start: str
) -> Response:
    machine = await _machine(db, machine_id)
    if machine.status == MachineStatus.BROKEN:
        raise MachineNotAvailable(t.ERR_MACHINE_BROKEN.format(machine=machine.name))

    now = datetime.now(UTC)
    starts_at = parse_start(start)
    if starts_at <= now:
        raise InvalidReservationTime(t.ERR_RESERVATION_PAST)
    if starts_at > schedule_svc.horizon_end(now):
        raise InvalidReservationTime(
            t.ERR_RESERVATION_HORIZON.format(days=settings.reservation_horizon_days)
        )
    hours = await workhours_svc.get(db, machine.room_id)
    if not schedule_svc.is_open_at(starts_at, hours):
        raise InvalidReservationTime(t.ERR_RESERVATION_WORK_HOURS.format(hours=hours.text()))
    # Опять же: форма, которая заведомо кончится отказом, не должна открываться.
    if await reservations_svc.slot_taken(db, machine.id, starts_at):
        raise ReservationOverlap(
            t.ERR_RESERVATION_OVERLAP.format(machine=machine.name, time=hhmm(starts_at))
        )

    # Закрытие мастерской длительность не урезает: работа идёт сама, и у брони
    # с 19:00 кнопка «до утра» — самая нужная. Потолок ставит только ближайшая
    # чужая бронь.
    upcoming = await reservations_svc.next_for_machine(db, machine.id, starts_at)
    limit = (
        int((upcoming.starts_at - starts_at).total_seconds() // 60)
        if upcoming is not None
        else None
    )
    return templates.TemplateResponse(
        request,
        "book.html",
        {
            "machine": machine,
            "starts_at": starts_at,
            "durations": duration_options(
                starts_at,
                limit_minutes=limit,
                minimum=settings.reservation_min_minutes,
            ),
            "grace": settings.reservation_grace_minutes,
            **client.context,
        },
    )


async def do_book(
    db: AsyncSession,
    client: Client,
    user: User,
    machine_id: int,
    start: str,
    minutes: int,
) -> Response:
    result = await reservations_svc.book(
        db, user, machine_id, parse_start(start), minutes
    )
    await db.commit()
    room = await db.get(Room, result.room_id)
    await notify.send_to_user(
        db,
        user.id,
        texts.booked(
            result.machine_name,
            room.name if room else "",
            result.starts_at,
            result.ends_at,
        ),
    )
    return client.done("booked")


async def booking_cancel_page(
    request: Request, db: AsyncSession, client: Client, reservation_id: int
) -> Response:
    booking = await reservations_svc.get_active(db, reservation_id)
    machine = await db.get(Machine, booking.machine_id)
    return templates.TemplateResponse(
        request,
        "confirm.html",
        {
            "title": t.UI["my_cancel"],
            "hint": t.UI["book_cancel_hint"],
            "subject": t.UI["admin_booking_row"].format(
                machine=machine.name if machine else "",
                start=when(booking.starts_at),
                end=hhmm(booking.ends_at),
            ),
            "action": f"{client.base}/booking/{booking.id}/cancel",
            "submit": t.UI["my_cancel"],
            **client.context,
        },
    )


async def do_booking_cancel(
    db: AsyncSession, client: Client, user: User, reservation_id: int
) -> Response:
    result = await reservations_svc.cancel(db, user, reservation_id)
    await db.commit()

    if not result.by_owner:
        await notify.send_to_user(
            db,
            result.user_id,
            texts.booking_cancelled_by_admin(result.machine_name, result.starts_at),
        )
    await notify.announce_offers(db, result.offers)
    return client.done("booking_cancelled")


async def my_page(
    request: Request, db: AsyncSession, client: Client, user: User
) -> Response:
    """Свои брони. Экран есть только там, где известно, кто смотрит."""
    return templates.TemplateResponse(
        request,
        "my.html",
        {
            "person": user,
            "bookings": await reservations_svc.of_user(db, user.id),
            "links": await schedule_links(db),
            **client.context,
        },
    )


async def schedule_links(db: AsyncSession) -> list[tuple[Room, str]]:
    """Пары (помещение, тип), у которых есть расписание.

    Спрашиваем парк, а не `MachineKind` и не список помещений: ссылка на
    расписание гравировщиков там, где их нет, ведёт на пустую сетку.
    """
    park = await machines_svc.list_machines(db)
    rooms = {room.id: room for room in await rooms_svc.list_rooms(db)}

    links: list[tuple[Room, str]] = []
    for machine in park:
        room = rooms.get(machine.room_id)
        if room is not None and (room, machine.kind) not in links:
            links.append((room, machine.kind))
    return links
