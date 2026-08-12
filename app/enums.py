from enum import StrEnum


class MachineKind(StrEnum):
    """Тип оборудования.

    Парк неоднороден: 3D-принтер печатает часами и без человека, гравировщик —
    другая машина с другой очередью. Тип определяет, к какой очереди относится
    ожидание (правило 3) и в какой секции экрана машина показана.

    Новый тип — это строка здесь, надписи в `MACHINE_KIND_*` (app/texts) и
    значение в CHECK-ограничении `machines_kind_valid`.
    """

    PRINTER = "printer"
    ENGRAVER = "engraver"


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
    WAITING = "waiting"
    OFFERED = "offered"
    TAKEN = "taken"
    EXPIRED = "expired"
    LEFT = "left"


# Статусы, которые считаются «занимающими» машину и человека.
# Дублируются в частичных уникальных индексах в migrations/versions/0001_initial.py —
# при изменении правь оба места.
ACTIVE_SESSION_STATUSES = (SessionStatus.PRINTING, SessionStatus.DONE_WAIT)
ACTIVE_QUEUE_STATUSES = (QueueStatus.WAITING, QueueStatus.OFFERED)
