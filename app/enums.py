from enum import StrEnum


class RoomKind(StrEnum):
    """Тип помещения.

    `WORKSHOP` — мастерская: в ней стоит оборудование, и человек приходит
    работать за конкретной машиной. `MEETING` — переговорная: бронируют её саму,
    и «оборудование» там одно — сама комната (см. `ROOM_KIND_MACHINE_KINDS`).

    Тип помещения решает, какие единицы в нём вообще можно завести, и какими
    словами о нём говорят экраны. Всё остальное — занятие, освобождение, бронь
    и журнал — у мастерской и переговорной устроено одинаково, и
    второй копии этой логики нет намеренно: разошлись бы на первой же правке.

    Новый тип — строка здесь, набор единиц в `ROOM_KIND_MACHINE_KINDS`, надписи
    в `ROOM_KIND_*` (app/texts) и значение в CHECK `rooms_kind_valid`.
    """

    WORKSHOP = "workshop"
    MEETING = "meeting"


class MachineKind(StrEnum):
    """Тип единицы, которую занимают и бронируют.

    Парк неоднороден: 3D-принтер печатает часами и без человека, гравировщик —
    другая машина. Тип определяет секцию экрана и расписания.

    `MEETING_ROOM` — переговорная как единица брони. Отдельной таблицы под неё
    нет по той же причине, по которой гравировщик лежит рядом с принтером:
    занять, освободить, забронировать и попасть в журнал у комнаты и у машины —
    одно и то же действие. Различает их тип, а слова
    подставляют тексты.

    Новый тип — это строка здесь, надписи в `MACHINE_KIND_*` (app/texts),
    место в `ROOM_KIND_MACHINE_KINDS` и значение в CHECK-ограничениях
    `machines_kind_valid` и `queue_kind_valid`.
    """

    PRINTER = "printer"
    ENGRAVER = "engraver"
    MEETING_ROOM = "meeting_room"


# Какие единицы можно завести в помещении какого типа. Пара «принтер в
# переговорной» — это не запрет ради запрета: тип единицы решает, что написано
# на плитке, и принтер посреди переговорной сделал бы
# из её расписания расписание печати.
#
# Проверяется в `services/machines.create` — в БД такого ограничения нет: CHECK
# не умеет смотреть в соседнюю таблицу, а гонки здесь нет (тип единицы выбирает
# админ один раз при заведении, и переезда между помещениями у машин нет).
ROOM_KIND_MACHINE_KINDS: dict[str, tuple[str, ...]] = {
    RoomKind.WORKSHOP: (MachineKind.PRINTER, MachineKind.ENGRAVER),
    RoomKind.MEETING: (MachineKind.MEETING_ROOM,),
}


class MachineStatus(StrEnum):
    """Состояние машины.

    `PRINTING` — историческое имя состояния «машина занята работой»: до
    появления гравировщиков парк состоял из одних принтеров. Значение в БД
    менять не стали — оно ничего не решает, а миграция задела бы CHECK и оба
    частичных уникальных индекса. Слово, которое видит человек, зависит от типа
    и лежит в `MACHINE_STATUS_WORDS` (app/texts).
    """

    FREE = "free"
    PRINTING = "printing"
    DONE_WAIT = "done_wait"  # работа закончилась, деталь на столе
    BROKEN = "broken"


class SessionStatus(StrEnum):
    PRINTING = "printing"
    DONE_WAIT = "done_wait"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class QueueStatus(StrEnum):
    """Исторические состояния удалённого сценария очереди."""

    WAITING = "waiting"
    OFFERED = "offered"
    TAKEN = "taken"
    EXPIRED = "expired"
    LEFT = "left"


class ReservationStatus(StrEnum):
    """Состояние брони на будущее.

    `TAKEN` — человек
    пришёл и начал работу, `EXPIRED` — не пришёл за отведённые минуты.
    """

    BOOKED = "booked"
    TAKEN = "taken"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


# Статусы, которые считаются «занимающими» машину и человека.
# Дублируются в частичных уникальных индексах в migrations/versions/0001_initial.py —
# при изменении правь оба места.
ACTIVE_SESSION_STATUSES = (SessionStatus.PRINTING, SessionStatus.DONE_WAIT)
ACTIVE_QUEUE_STATUSES = (QueueStatus.WAITING, QueueStatus.OFFERED)
# Ожидающая своего часа бронь — одна на человека и без пересечений на машине.
# Дублируется в migrations/versions/0006_reservations.py.
ACTIVE_RESERVATION_STATUSES = (ReservationStatus.BOOKED,)
