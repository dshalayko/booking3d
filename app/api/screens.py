"""Экраны и действия — один набор на киоск и на Mini App.

Планшет на стене и телефон в Telegram показывают одно и то же: список помещений,
доску помещения, расписание, формы занятия и брони. Отличаются они тремя вещами,
и все три описаны в `Client`: префикс адресов, нужен ли PIN и известно ли, кто
смотрит. Всё остальное — этот модуль.

Помещение есть в адресе почти каждого экрана: доска и расписание у каждого
свои. Планшет своё помещение знает (оно записано в его device-cookie,
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
    # Известно ли, кто смотрит. В Mini App — да (подпись Telegram), на стене —
    # нет: планшет общий, и личность там живёт ровно один POST с PIN.
    #
    # От этого зависит, куда человек уходит после действия. На личном экране его
    # оставляют там, где видны свои брони. На общем экране такого списка нет —
    # он показал бы чужие данные, поэтому планшет возвращается к доске.
    personal: bool = False

    @property
    def context(self) -> dict:
        return {
            "base": self.base,
            "needs_pin": self.needs_pin,
            "telegram_sdk": self.telegram_sdk,
        }

    def done(self, flash: str, path: str = "/") -> RedirectResponse:
        """Куда уходит человек после действия.

        По умолчанию — на главный экран: там видно, что изменилось, и оттуда
        начинается любое следующее действие. `path` нужен действиям, у которых
        результат живёт на своей странице, — см. `do_book`.
        """
        return RedirectResponse(
            f"{self.base}{path}?flash={flash}", status_code=status.HTTP_303_SEE_OTHER
        )


KIOSK = Client(base="", needs_pin=True)
APP = Client(base="/app", needs_pin=False, telegram_sdk=True, personal=True)
# Админка своим экраном ошибки не занимается — его рисует main.py. Клиента ей
# хватает ради одного: кнопка «Понятно» должна вести обратно в панель.
ADMIN = Client(base="/admin", needs_pin=False)


def client_for(path: str) -> Client:
    """Чей это адрес — планшета, телефона или панели.

    Нужно экрану ошибки: он рисуется в обработчике (main.py), вне любого из
    экранов ниже, и без этого доставался бы киосковым по умолчанию — с адресами
    на `/` и с авто-возвратом туда же (api/render.py, глобальные Jinja). Внутри
    Telegram это тупик: `/` — это доска на стене с клавиатурой PIN, которого
    телефону всё равно не примут (правило 11), и выйти из неё можно только
    закрыв и открыв приложение заново из чата.
    """
    if path == APP.base or path.startswith(f"{APP.base}/"):
        return APP
    if path == ADMIN.base or path.startswith(f"{ADMIN.base}/"):
        return ADMIN
    return KIOSK


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
    машины по всем помещениям сразу (services/board.py,
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
        "can_book": (
            True
            if viewer is None
            else await reservations_svc.can_user_book(db, viewer.id)
        ),
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
    """«Моё» — доска, сжатая до занятого, по всем помещениям.

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
    return client.done("released")


# --- расписание и брони ------------------------------------------------------


async def schedule_page(
    request: Request,
    db: AsyncSession,
    client: Client,
    room_id: int,
    kind: str,
    day_value: str = "",
    viewer: User | None = None,
    flash: str = "",
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
        request,
        "schedule.html",
        {
            "grid": grid,
            "can_book": (
                True
                if viewer is None
                else await reservations_svc.can_user_book(db, viewer.id)
            ),
            "flash": t.FLASH_KIOSK.get(flash),
            **client.context,
        },
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
    # Забронировав, человек уходит к своим бронам, а не на главный экран: он
    # только что назначил себе время, и первое, что нужно увидеть, — что оно
    # действительно записано и какое именно. Оттуда же его и отменяют. На стене
    # такого экрана нет — планшет общий, и список чужих брон на нём не место.
    return client.done("booked", "/my" if client.personal else "/")


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
            "hint": t.UI["book_cancel_hint" if client.needs_pin else "book_cancel_hint_app"],
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
    return client.done("booking_cancelled")


async def my_page(
    request: Request, db: AsyncSession, client: Client, user: User, flash: str = ""
) -> Response:
    """Свои брони. Экран есть только там, где известно, кто смотрит.

    Сюда же приводит бронирование, поэтому экран умеет показать плашку: без неё
    человек попадает на список и не понимает, случилось ли что-то только что.
    """
    can_book = await reservations_svc.can_user_book(db, user.id)
    return templates.TemplateResponse(
        request,
        "my.html",
        {
            "person": user,
            "bookings": await reservations_svc.of_user(
                db, user.id, include_in_progress=True
            ),
            "links": await schedule_links(db) if can_book else [],
            "can_book": can_book,
            "flash": t.FLASH_KIOSK.get(flash),
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
