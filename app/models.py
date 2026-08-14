from datetime import datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.enums import MachineKind, MachineStatus, ReservationStatus, RoomKind


class Base(DeclarativeBase):
    pass


class Room(Base):
    """Помещение: мастерская с оборудованием или переговорная.

    Появилось, когда система перестала быть системой одной комнаты. Помещение —
    это не украшение экрана, а граница, внутри которой считаются правила:
    очередь общая на машины одного типа *в этом помещении* (правило 3), и одна
    работа, одно место в очереди и одна бронь на человека — тоже в пределах
    помещения (правила 2 и 13). Иначе бронь переговорной запрещала бы печать, а
    ожидающий принтер в одном корпусе получал бы приглашение в другой.

    Часы работы у каждого помещения свои (правило 15): переговорная закрывается
    в шесть, а мастерская работает до ночи. См. `WorkHours`.
    """

    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Имя уникально: человек видит его на планшете, в списке помещений и в
    # сообщении бота, и две «Переговорные» рядом означали бы, что он не знает,
    # куда идти. Та же причина, что у имени машины.
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{RoomKind.WORKSHOP}'")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Room {self.id} {self.name} {self.kind}>"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # HMAC от PIN: уникальный, потому что на киоске человек вводит только его,
    # и по нему нужно однозначно опознать, кто это. См. services/security.py.
    pin_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<User {self.id} {self.name}>"


class WorkHours(Base):
    """Часы работы помещения — одна строка на помещение.

    В .env их держать нельзя: часы меняет тот, кто отвечает за помещение, а не
    тот, у кого есть ssh на сервер. Поэтому таблица и форма в админке.

    Одна строка на помещение, а не на день недели: переговорная и мастерская
    открыты по-разному, и это разница, которую видно каждый день, — а «в субботу
    до шести» пока нет, и решать её заранее значит рисовать семь полей ради
    одного используемого.

    Строки может не быть вовсе: заведённое помещение работает по значениям из
    `services/workhours.py`, пока часы не сохранили руками. Так помещение можно
    создать одним полем, а не заполняя форму часов ради того, чтобы оно вообще
    появилось на экране.

    Время местное и без зоны: 08:00 — это то, что написано на двери, а в какой
    момент UTC оно случится, считает services/schedule.py по `settings.zone`.
    """

    __tablename__ = "work_hours"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Помещение, чьи это часы. Уникально — строка ровно одна на помещение;
    # удаление помещения забирает часы с собой (ON DELETE CASCADE), иначе от
    # удалённой комнаты остались бы часы, которые никто уже не откроет.
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    opens_at: Mapped[time] = mapped_column(Time, nullable=False)
    # 00:00 означает «до полуночи», а не «нулевой длины»: иначе круглосуточную
    # мастерскую нельзя было бы описать вовсе. Разбирает `schedule.work_bounds`.
    closes_at: Mapped[time] = mapped_column(Time, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<WorkHours room={self.room_id} {self.opens_at}–{self.closes_at}>"


class Machine(Base):
    """Единица парка: 3D-принтер, гравировщик или сама переговорная.

    Одна таблица на все типы, а не таблица на тип: занятие, освобождение,
    поломка, очередь и журнал устроены одинаково, и вторая копия этой логики
    разошлась бы с первой на первой же правке. Различает машины `kind`.

    Переговорная лежит здесь же — строкой с типом `meeting_room` в помещении
    типа `meeting`. Комнату занимают, освобождают и бронируют теми же
    действиями, что принтер, поэтому отдельной «брони помещения» в системе нет.
    """

    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(primary_key=True)
    # В каком помещении стоит. Не nullable: машина без помещения не попала бы ни
    # на один экран — доска, расписание и очередь начинаются с помещения.
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    # Имя уникально по всему парку, а не внутри помещения: человек видит его на
    # экране и в сообщении бота без пометки, где машина стоит, и два «№1» рядом
    # означали бы, что подошедший не знает, к какой машине идти.
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{MachineKind.PRINTER}'")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{MachineStatus.FREE}'")
    )
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Machine {self.name} {self.kind} {self.status}>"


class MachineSession(Base):
    """Одно занятие машины человеком. Таблица называется sessions."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
    # Помещение машины, скопированное в саму работу. Копия, а не join: правило 2
    # («одна работа на человека в помещении») держит частичный уникальный индекс,
    # а индекс не умеет смотреть в соседнюю таблицу. Машина из помещения в
    # помещение не переезжает (services/machines.py), так что копия не разойдётся
    # с оригиналом.
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    eta_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    freed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    # Из какой брони выросла работа. Пусто у обычного «занять сейчас».
    reservation_id: Mapped[int | None] = mapped_column(ForeignKey("reservations.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Отметки об отправленных напоминаниях: планировщик сверяет состояние с
    # часами каждую минуту, и это защита от повторной отправки.
    warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unclaimed_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Правило 1: одна активная сессия на машину.
        Index(
            "one_active_session_per_machine",
            "machine_id",
            unique=True,
            postgresql_where=text("status IN ('printing', 'done_wait')"),
        ),
        # Правило 2: одна активная работа на человека в помещении — на все типы
        # разом, а не на тип. Занял принтер — освободи, прежде чем брать
        # гравировщик рядом. А переговорная в другом помещении печати не мешает:
        # это разные комнаты и разные дела.
        Index(
            "one_active_session_per_user",
            "user_id",
            "room_id",
            unique=True,
            postgresql_where=text("status IN ('printing', 'done_wait')"),
        ),
    )

    def __repr__(self) -> str:
        return f"<MachineSession {self.id} machine={self.machine_id} {self.status}>"


class QueueEntry(Base):
    __tablename__ = "queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # Где человек ждёт. Очередь — это (помещение, тип): освободившийся принтер в
    # соседнем корпусе не нужен тому, кто сидит в этом, а приглашение туда
    # придержало бы машину на всё окно подтверждения впустую.
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    # Тип, которого человек ждёт. Очередь на принтеры и очередь на гравировщики
    # независимы: освободившийся гравировщик не должен уходить тому, кому нужна
    # печать, — он бы отказался, а машина простояла бы окно подтверждения.
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{MachineKind.PRINTER}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    offered_machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id"))
    offered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offer_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Когда запись закрылась: занял, окно истекло или вышел сам.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Правило 2: одно место в очереди на человека в помещении — на все его
        # типы разом. Стоять сразу в двух очередях одной комнаты бессмысленно:
        # занять там всё равно получится только что-то одно, а второе
        # приглашение сгорело бы впустую, придержав машину на всё окно.
        Index(
            "one_queue_entry_per_user",
            "user_id",
            "room_id",
            unique=True,
            postgresql_where=text("status IN ('waiting', 'offered')"),
        ),
        # Порядок очереди: FIFO по created_at среди активных своей пары
        # (помещение, тип) — ею очередь и выбирается, поэтому оба поля впереди.
        Index("queue_active_order", "room_id", "kind", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<QueueEntry {self.id} user={self.user_id} "
            f"room={self.room_id} {self.kind} {self.status}>"
        )


class Reservation(Base):
    """Бронь машины на конкретное окно в будущем.

    Очередь отвечает на вопрос «кто следующий, когда освободится», бронь — на
    вопрос «мне нужно к утру четверга». Поэтому бронь всегда на конкретную
    машину, а не на тип: человек приходит в назначенный час к назначенному
    столу, и «какой-нибудь принтер этого типа» здесь ничего не гарантирует.

    Правило 12: в своё окно машину занимает только тот, кто её забронировал —
    это право сильнее очереди (правило 7).
    """

    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), nullable=False)
    # Помещение машины — копия, как и у работы: правило 13 («одна бронь на
    # человека в помещении») держит частичный уникальный индекс, а тот не умеет
    # смотреть в `machines`.
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Когда бронь закрылась: пришёл, не пришёл или отменил.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    cancelled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # Отметки об отправленных напоминаниях — как у сессии: планировщик сверяет
    # состояние с часами каждую минуту, и без них сообщение ушло бы повторно.
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Правило 13: одна бронь на человека в помещении. Парк маленький, и без
        # ограничения один человек забивает собой всю неделю вперёд. Бронь
        # переговорной при этом печати не мешает: это другое помещение.
        Index(
            "one_active_reservation_per_user",
            "user_id",
            "room_id",
            unique=True,
            postgresql_where=text(f"status = '{ReservationStatus.BOOKED}'"),
        ),
        # Выборка «что забронировано на этой машине после такого-то часа» —
        # самая частая: она рисует расписание и урезает «занять сейчас».
        Index("reservations_machine_time", "machine_id", "starts_at"),
    )

    # Непересечение брон на одной машине несёт EXCLUDE-ограничение
    # `reservations_no_overlap` (см. migrations/versions/0006_reservations.py):
    # в SQLAlchemy его не выразить, а проверкой в коде — значит проиграть гонке
    # двух одновременных бронирований.

    def __repr__(self) -> str:
        return f"<Reservation {self.id} machine={self.machine_id} {self.status}>"
