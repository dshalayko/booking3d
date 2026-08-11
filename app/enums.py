from enum import StrEnum


class PrinterStatus(StrEnum):
    FREE = "free"
    PRINTING = "printing"
    DONE_WAIT = "done_wait"  # печать закончилась, деталь на столе
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


# Статусы, которые считаются «занимающими» принтера и человека.
# Дублируются в частичных уникальных индексах в migrations/versions/0001_initial.py —
# при изменении правь оба места.
ACTIVE_SESSION_STATUSES = (SessionStatus.PRINTING, SessionStatus.DONE_WAIT)
ACTIVE_QUEUE_STATUSES = (QueueStatus.WAITING, QueueStatus.OFFERED)
