"""Переключаемые пользовательские квоты на работы и брони.

Ожидающая бронь и активная работа расходуют одну и ту же квоту. Это не даёт
обойти лимит, заняв один станок сейчас и забронировав остальные на будущее.
Своя бронь и начатая на той же машине работа считаются одной задачей — такое
занятие является использованием уже обещанной машины, а не новой заявкой.
"""

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ACTIVE_RESERVATION_STATUSES, ACTIVE_SESSION_STATUSES, MachineKind
from app.models import BookingPolicy, Machine, MachineSession, Reservation

POLICY_ID = 1
MULTI_LIMITS = {
    MachineKind.PRINTER: 2,
    MachineKind.ENGRAVER: 1,
}


@dataclass(frozen=True)
class UserLoad:
    counts: Counter[str]
    total: int
    reservation_machine_ids: frozenset[int]
    has_session: bool
    has_reservation: bool


async def enabled(db: AsyncSession) -> bool:
    policy = await db.get(BookingPolicy, POLICY_ID)
    return bool(policy and policy.multi_machine_enabled)


async def save(db: AsyncSession, value: bool) -> BookingPolicy:
    policy = await db.get(BookingPolicy, POLICY_ID, with_for_update=True)
    if policy is None:
        policy = BookingPolicy(id=POLICY_ID, multi_machine_enabled=value)
        db.add(policy)
    else:
        policy.multi_machine_enabled = value
    await db.flush()
    return policy


async def load(db: AsyncSession, user_id: int) -> UserLoad:
    reservation_rows = (
        await db.execute(
            select(Reservation.machine_id, Machine.kind)
            .join(Machine, Machine.id == Reservation.machine_id)
            .where(
                Reservation.user_id == user_id,
                Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
            )
        )
    ).all()
    session_rows = (
        await db.execute(
            select(MachineSession.machine_id, Machine.kind)
            .join(Machine, Machine.id == MachineSession.machine_id)
            .where(
                MachineSession.user_id == user_id,
                MachineSession.status.in_(ACTIVE_SESSION_STATUSES),
            )
        )
    ).all()

    reservation_machine_ids = frozenset(machine_id for machine_id, _ in reservation_rows)
    kinds = [kind for _, kind in reservation_rows]
    # Если человек занял заранее забронированную машину, это одна задача. При
    # раннем занятии бронь ещё BOOKED, поэтому дедупликация нужна именно здесь.
    kinds.extend(
        kind for machine_id, kind in session_rows if machine_id not in reservation_machine_ids
    )
    return UserLoad(
        counts=Counter(kinds),
        total=len(kinds),
        reservation_machine_ids=reservation_machine_ids,
        has_session=bool(session_rows),
        has_reservation=bool(reservation_rows),
    )


def _within_multi_limit(current: UserLoad, kind: str) -> bool:
    limit = MULTI_LIMITS.get(kind)
    if limit is None:
        # Переговорные и будущие типы сохраняют строгий режим и не смешиваются
        # с оборудованием мастерской.
        return current.total == 0
    if any(
        current.counts[used_kind]
        for used_kind in current.counts
        if used_kind not in MULTI_LIMITS
    ):
        return False
    return current.counts[kind] < limit


async def available_kinds(db: AsyncSession, user_id: int) -> set[str]:
    """Типы, для которых у пользователя осталась квота."""
    current = await load(db, user_id)
    multi = await enabled(db)
    kinds = set(MachineKind)
    if not multi:
        return kinds if current.total == 0 else set()
    return {kind for kind in kinds if _within_multi_limit(current, kind)}


async def can_start_machine(
    db: AsyncSession, user_id: int, machine: Machine
) -> tuple[bool, UserLoad, bool]:
    """Можно ли добавить задачу; третий результат — включён ли расширенный режим."""
    current = await load(db, user_id)
    multi = await enabled(db)
    # Занятие своей забронированной машины не расходует ещё одну квоту.
    if machine.id in current.reservation_machine_ids:
        return True, current, multi
    if not multi:
        return current.total == 0, current, multi
    return _within_multi_limit(current, machine.kind), current, multi


async def can_book_machine(
    db: AsyncSession, user_id: int, machine: Machine
) -> tuple[bool, UserLoad, bool]:
    current = await load(db, user_id)
    multi = await enabled(db)
    if not multi:
        return current.total == 0, current, multi
    return _within_multi_limit(current, machine.kind), current, multi
