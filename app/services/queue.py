"""Очередь.

Ради неё система и существует: машины заняты работами по 4–12 часов, значит
большую часть времени свободных нет, и ценность в ответе на вопрос «когда
освободится и я ли следующий».

Правила отсюда (PLAN.md):
  3. очередь общая на все машины одного типа в помещении;
  4. предложение получает только первый, а не рассылка всем;
  5. окно подтверждения 30 минут;
  6. ночью окно не тикает.

Про типы и помещения. Очередь — это пара (помещение, тип): ждущий гравировщик и
ждущий принтер стоят в разных списках, и ждущий принтер в одном корпусе — в
третьем. Один список на весь парк означал бы, что освободившийся принтер
предлагается человеку с файлом для гравировки или человеку в другом здании — он
откажется или не дойдёт, а машина простоит впустую всё окно подтверждения. Место
в очереди при этом одно на человека в помещении (правило 2): занять там всё
равно можно только что-то одно.

Функции ничего не отправляют в Telegram: они возвращают описание того, что
произошло, а отправкой занимается вызывающий слой (шаги 6–7). Так домен
остаётся тестируемым без бота.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.config import settings
from app.enums import (
    ACTIVE_QUEUE_STATUSES,
    ACTIVE_RESERVATION_STATUSES,
    ACTIVE_SESSION_STATUSES,
    ROOM_KIND_MACHINE_KINDS,
    MachineKind,
    MachineStatus,
    QueueStatus,
)
from app.models import Machine, MachineSession, QueueEntry, Reservation
from app.services import rooms as rooms_svc
from app.services.errors import (
    AlreadyBooked,
    AlreadyInQueue,
    MachineKindNotInRoom,
    MachineKindUnknown,
    NotInQueue,
    OfferNotActive,
    UserBusy,
)
from app.services.schedule import MIN_DURATION_MINUTES
from app.services.timeutil import add_active_minutes


@dataclass(frozen=True)
class Offer:
    """Предложение занять освободившуюся машину, ушедшее первому в очереди."""

    entry_id: int
    user_id: int
    machine_id: int
    machine_name: str
    room_id: int
    kind: str
    expires_at: datetime


@dataclass(frozen=True)
class JoinResult:
    entry_id: int
    room_id: int
    kind: str
    position: int
    offers: list[Offer]


@dataclass(frozen=True)
class LeaveResult:
    entry_id: int
    offers: list[Offer]


@dataclass(frozen=True)
class ExpireResult:
    entry_id: int
    user_id: int
    machine_id: int
    offers: list[Offer]


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def join(
    db: AsyncSession, user_id: int, room_id: int, kind: str, now: datetime | None = None
) -> JoinResult:
    """Встать в очередь на машины указанного типа в этом помещении.

    Человек с активной работой в этом помещении в очередь не встаёт: иначе один
    занимает машину и держит место на вторую, а при небольшом парке это его
    монополия. Работа в соседнем помещении не мешает — там своя очередь и свой
    лимит (правило 2).
    """
    if kind not in tuple(MachineKind):
        raise MachineKindUnknown(t.ERR_MACHINE_KIND_UNKNOWN.format(kind=kind))

    now = now or _utcnow()

    # Тип должен подходить помещению: очередь на принтер в переговорной — это
    # ожидание машины, которая там не появится.
    room = await rooms_svc.get(db, room_id)
    if kind not in ROOM_KIND_MACHINE_KINDS.get(room.kind, ()):
        raise MachineKindNotInRoom(
            t.ERR_MACHINE_KIND_NOT_IN_ROOM.format(
                kind=t.MACHINE_KIND_ONE.get(kind, kind), room=room.name
            )
        )

    active_session = await db.scalar(
        select(MachineSession.id).where(
            MachineSession.user_id == user_id,
            MachineSession.room_id == room_id,
            MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
    )
    if active_session is not None:
        raise UserBusy(t.ERR_USER_BUSY_FREE_FIRST)

    # Бронь — то же дело, что работа: час на эту машину человеку уже обещан, и
    # место в очереди сверх него — это вторая заявка на тот же парк. Запросом по
    # `Reservation` напрямую, а не через services/reservations.py: тот зовёт
    # очередь сам (`offer_free_machines`), и обратный импорт замкнул бы кольцо.
    booked = await db.scalar(
        select(Reservation.id).where(
            Reservation.user_id == user_id,
            Reservation.room_id == room_id,
            Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
        )
    )
    if booked is not None:
        raise AlreadyBooked(t.ERR_QUEUE_WHILE_BOOKED)

    existing = await _active_entry(db, user_id, room_id)
    if existing is not None:
        raise AlreadyInQueue(t.ERR_ALREADY_IN_QUEUE)

    entry = QueueEntry(
        user_id=user_id,
        room_id=room_id,
        kind=kind,
        status=QueueStatus.WAITING,
        created_at=now,
    )
    db.add(entry)
    await db.flush()

    # Если прямо сейчас есть свободная машина этого типа, предложение уйдёт
    # сразу же.
    offers = await offer_free_machines(db, now)
    position = await position_of(db, user_id, room_id) or 1
    return JoinResult(
        entry_id=entry.id, room_id=room_id, kind=kind, position=position, offers=offers
    )


async def leave(
    db: AsyncSession, user_id: int, room_id: int, now: datetime | None = None
) -> LeaveResult:
    """Выйти из очереди этого помещения. Предложение уходит следующему.

    Тип не спрашиваем: место в очереди у человека в помещении одно, и какое
    именно — видно по записи. А помещение спрашиваем: очередей у человека может
    быть столько, сколько комнат, и угадывать, из какой он выходит, нельзя.
    """
    now = now or _utcnow()

    entry = await _active_entry(db, user_id, room_id)
    if entry is None:
        raise NotInQueue(t.ERR_NOT_IN_QUEUE)

    # `offered_machine_id` не затираем: активным предложение делает статус, а не
    # заполненное поле, зато в журнале останется, на какую машину приглашали.
    entry.status = QueueStatus.LEFT
    entry.resolved_at = now
    await db.flush()

    offers = await offer_free_machines(db, now)
    return LeaveResult(entry_id=entry.id, offers=offers)


async def offer_free_machines(db: AsyncSession, now: datetime | None = None) -> list[Offer]:
    """Раздать свободные машины первым в очереди их помещения и типа.

    Правило 4: на каждую свободную машину уходит ровно одно предложение первому
    ожидающему этот тип в этом помещении. Машина при этом остаётся в статусе
    `free` — она физически свободна, но занять её может только адресат
    предложения (правило 7).

    Забронированные машины не раздаются (правило 12): в чужое окно занять их всё
    равно нельзя, и предложение сгорело бы впустую, придержав машину на все 30
    минут. Так же пропускаются машины, у которых до брони осталось меньше
    минимальной работы, — предложение на десять минут не предложение.
    """
    now = now or _utcnow()

    reserved = select(QueueEntry.offered_machine_id).where(
        QueueEntry.status == QueueStatus.OFFERED,
        QueueEntry.offered_machine_id.is_not(None),
    )
    # Запрос по `Reservation` напрямую, а не через services/reservations.py: тот
    # импортирует эту функцию, и обратный импорт замкнул бы кольцо.
    booked = select(Reservation.machine_id).where(
        Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
        Reservation.starts_at <= now + timedelta(minutes=MIN_DURATION_MINUTES),
        Reservation.ends_at > now,
    )
    free_machines = (
        await db.scalars(
            select(Machine)
            .where(
                Machine.status == MachineStatus.FREE,
                Machine.id.not_in(reserved),
                Machine.id.not_in(booked),
            )
            .order_by(Machine.id)
            .with_for_update()
        )
    ).all()

    offers: list[Offer] = []
    for machine in free_machines:
        entry = await db.scalar(
            select(QueueEntry)
            .where(
                QueueEntry.status == QueueStatus.WAITING,
                QueueEntry.room_id == machine.room_id,
                QueueEntry.kind == machine.kind,
            )
            .order_by(QueueEntry.created_at, QueueEntry.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if entry is None:
            # Эта очередь пуста — машина просто стоит свободной. Не `break`: у
            # машин другого типа и в других помещениях очередь может быть
            # непустой.
            continue

        entry.status = QueueStatus.OFFERED
        entry.offered_machine_id = machine.id
        entry.offered_at = now
        # Правило 6: ночью окно не тикает, иначе предложение сгорит в 03:40
        # впустую и к утру очередь опустеет, никого не пустив.
        entry.offer_expires_at = add_active_minutes(
            now,
            settings.offer_window_minutes,
            settings.night_start,
            settings.night_end,
            tz=settings.zone,
        )
        await db.flush()

        offers.append(
            Offer(
                entry_id=entry.id,
                user_id=entry.user_id,
                machine_id=machine.id,
                machine_name=machine.name,
                room_id=machine.room_id,
                kind=machine.kind,
                expires_at=entry.offer_expires_at,
            )
        )

    return offers


async def expire_offer(
    db: AsyncSession, entry_id: int, now: datetime | None = None
) -> ExpireResult:
    """Закрыть просроченное предложение и передать машину следующему."""
    now = now or _utcnow()

    entry = await db.get(QueueEntry, entry_id, with_for_update=True)
    if entry is None or entry.status != QueueStatus.OFFERED:
        raise OfferNotActive(t.ERR_OFFER_NOT_ACTIVE)
    if entry.offer_expires_at is not None and entry.offer_expires_at > now:
        raise OfferNotActive(t.ERR_OFFER_WINDOW_OPEN)

    machine_id = entry.offered_machine_id
    entry.status = QueueStatus.EXPIRED
    entry.resolved_at = now
    await db.flush()

    offers = await offer_free_machines(db, now)
    return ExpireResult(
        entry_id=entry.id,
        user_id=entry.user_id,
        machine_id=machine_id,
        offers=offers,
    )


async def active_entries(
    db: AsyncSession, room_id: int | None = None, kind: str | None = None
) -> list[QueueEntry]:
    """Очередь в порядке FIFO — то, что видно на киоске.

    Без аргументов — все ожидания подряд, для журнала админки. С парой
    (`room_id`, `kind`) — та самая очередь, номера в которой человек видит на
    экране.
    """
    query = select(QueueEntry).where(QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES))
    if room_id is not None:
        query = query.where(QueueEntry.room_id == room_id)
    if kind is not None:
        query = query.where(QueueEntry.kind == kind)
    return list(
        (await db.scalars(query.order_by(QueueEntry.created_at, QueueEntry.id))).all()
    )


async def entries_of_user(db: AsyncSession, user_id: int) -> list[QueueEntry]:
    """Все активные ожидания человека — по одному на помещение (правило 2).

    Нужны боту и экрану «моё»: с несколькими помещениями «место в очереди» уже
    не единственное, и показать одно из них значило бы соврать про остальные.
    """
    return list(
        (
            await db.scalars(
                select(QueueEntry)
                .where(
                    QueueEntry.user_id == user_id,
                    QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES),
                )
                .order_by(QueueEntry.created_at, QueueEntry.id)
            )
        ).all()
    )


async def position_of(db: AsyncSession, user_id: int, room_id: int) -> int | None:
    """Позиция человека в очереди этого помещения. None — если его там нет.

    Считается внутри своей пары (помещение, тип): третий в очереди на
    гравировщик — третий, а не пятый из-за двоих, ждущих принтер, и не седьмой
    из-за очереди в соседнем корпусе.
    """
    entry = await _active_entry(db, user_id, room_id)
    if entry is None:
        return None
    for index, item in enumerate(
        await active_entries(db, room_id=room_id, kind=entry.kind), start=1
    ):
        if item.user_id == user_id:
            return index
    return None


async def position_in(
    db: AsyncSession, user_id: int, room_id: int, kind: str
) -> int | None:
    """Номер человека в этой самой очереди — (помещение, тип). None — его тут нет.

    От `position_of` отличается тем, что тип задан снаружи, а не взят из его
    ожидания. Правило 2 держит одно ожидание на помещение, но расписание всегда
    открыто на конкретном оборудовании, и про ожидание гравировщика на сетке
    принтеров оно должно молчать: «вы в очереди» над сеткой принтеров — это
    обещание машины, которую человек не ждал.
    """
    for index, entry in enumerate(
        await active_entries(db, room_id=room_id, kind=kind), start=1
    ):
        if entry.user_id == user_id:
            return index
    return None


async def offer_for_machine(db: AsyncSession, machine_id: int) -> QueueEntry | None:
    """Активное предложение на эту машину, если оно есть."""
    return await db.scalar(
        select(QueueEntry).where(
            QueueEntry.status == QueueStatus.OFFERED,
            QueueEntry.offered_machine_id == machine_id,
        )
    )


async def has_active_entries(
    db: AsyncSession, room_id: int | None = None, kind: str | None = None
) -> bool:
    query = select(QueueEntry.id).where(QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES))
    if room_id is not None:
        query = query.where(QueueEntry.room_id == room_id)
    if kind is not None:
        query = query.where(QueueEntry.kind == kind)
    return await db.scalar(query.limit(1)) is not None


async def _active_entry(db: AsyncSession, user_id: int, room_id: int) -> QueueEntry | None:
    return await db.scalar(
        select(QueueEntry).where(
            QueueEntry.user_id == user_id,
            QueueEntry.room_id == room_id,
            QueueEntry.status.in_(ACTIVE_QUEUE_STATUSES),
        )
    )
