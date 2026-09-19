"""Equipment time budgets. User-row locks serialize all spending and admin changes.

Usage is derived from sessions, so early release and cancelled reservations cannot
leave a stale debit. Only cycle boundaries and explicit admin overrides are stored.
Existing work/bookings are grandfathered at the initial activation boundary.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.config import settings
from app.enums import ACTIVE_RESERVATION_STATUSES, MachineKind
from app.models import (
    Machine,
    MachineSession,
    Reservation,
    UsageAccount,
    UsagePolicy,
    User,
)
from app.services.durations import hours_text
from app.services.errors import DomainError

KINDS = (MachineKind.PRINTER, MachineKind.ENGRAVER)


@dataclass
class Balance:
    enabled: bool = False
    unlimited: bool = False
    limit_minutes: int = 0
    used_seconds: float = 0
    held_seconds: float = 0
    cooldown_until: datetime | None = None

    @property
    def available_minutes(self) -> int:
        if self.cooldown_until:
            return 0
        return max(0, int((self.limit_minutes * 60 - self.used_seconds - self.held_seconds) // 60))

    @property
    def used_minutes(self) -> int:
        return int(self.used_seconds // 60)

    @property
    def held_minutes(self) -> int:
        return int((self.held_seconds + 59) // 60)

    @property
    def can_forfeit_remainder(self) -> bool:
        remaining = self.limit_minutes * 60 - self.used_seconds
        return (
            self.enabled
            and not self.unlimited
            and not self.cooldown_until
            and self.held_seconds == 0
            and 0 < remaining < 15 * 60
        )

    @property
    def message(self) -> str:
        if not self.enabled:
            return t.USAGE_DISABLED
        if self.unlimited:
            return t.USAGE_UNLIMITED
        if self.cooldown_until:
            return t.USAGE_COOLDOWN.format(
                until=self.cooldown_until.astimezone(settings.zone).strftime("%d.%m.%Y %H:%M"),
                zone=str(settings.zone),
            )
        return t.USAGE_BALANCE.format(
            available=hours_text(self.available_minutes),
            used=hours_text(self.used_seconds / 60),
            held=hours_text(self.held_seconds / 60),
            limit=hours_text(self.limit_minutes),
        )


def measure(intervals, start, now, budget):
    """Integrate overlapping machine intervals and find the exact exhaustion time."""
    events = {}
    for left, right in intervals:
        left, right = max(left, start), min(right, now)
        if right <= left:
            continue
        events[left] = events.get(left, 0) + 1
        events[right] = events.get(right, 0) - 1
    used = 0.0
    rate = 0
    previous = start
    for moment, delta in sorted(events.items()):
        amount = (moment - previous).total_seconds() * rate
        if rate and used + amount >= budget:
            return budget, previous + timedelta(seconds=(budget - used) / rate)
        used += amount
        rate += delta
        previous = moment
    return used, None


async def balance(
    db: AsyncSession,
    user_id: int,
    now: datetime | None = None,
    *,
    persist: bool = False,
    exclude_reservation: int | None = None,
) -> Balance:
    now = now or datetime.now(UTC)
    if persist:
        locked = await db.scalar(select(User.id).where(User.id == user_id).with_for_update())
        if locked is None:
            return Balance()
    policy = await db.get(UsagePolicy, 1, populate_existing=True)
    if policy is None or not policy.enabled:
        return Balance()
    account = await db.get(UsageAccount, user_id, populate_existing=True)
    if account is None:
        account = UsageAccount(
            user_id=user_id,
            unlimited=False,
            bonus_minutes=0,
            cycle_start=policy.activated_at,
        )
        if persist:
            db.add(account)
    if account.unlimited:
        return Balance(enabled=True, unlimited=True)
    start = max(account.cycle_start, policy.activated_at)
    until = account.cooldown_until
    bonus = account.bonus_minutes
    limit = account.limit_minutes or policy.limit_minutes
    rows = (
        await db.execute(
            select(MachineSession, Reservation.created_at)
            .join(Machine, Machine.id == MachineSession.machine_id)
            .outerjoin(Reservation, Reservation.id == MachineSession.reservation_id)
            .where(
                MachineSession.user_id == user_id,
                Machine.kind.in_(KINDS),
                MachineSession.started_at >= policy.activated_at,
                MachineSession.eta_at > start,
            )
        )
    ).all()
    sessions = [s for s, booked_at in rows if booked_at is None or booked_at >= policy.activated_at]
    intervals = [(s.started_at, min(s.eta_at, s.ended_at or s.eta_at)) for s in sessions]
    while True:
        if until:
            if now < until:
                used = (limit + bonus) * 60
                break
            start, until, bonus = until, None, 0
        used, exhausted = measure(intervals, start, now, (limit + bonus) * 60)
        if exhausted is None:
            break
        until = exhausted + timedelta(hours=policy.cooldown_hours)
    held = sum(
        max(0, (s.eta_at - max(now, s.started_at)).total_seconds())
        for s in sessions
        if s.ended_at is None
    )
    bookings = (
        await db.scalars(
            select(Reservation)
            .join(Machine, Machine.id == Reservation.machine_id)
            .where(
                Reservation.user_id == user_id,
                Machine.kind.in_(KINDS),
                Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
                Reservation.created_at >= policy.activated_at,
                Reservation.ends_at > now,
            )
        )
    ).all()
    session_bookings = {s.reservation_id for s in sessions}
    held += sum(
        (r.ends_at - r.starts_at).total_seconds()
        for r in bookings
        if r.id != exclude_reservation and r.id not in session_bookings
    )
    if persist:
        account.cycle_start = start
        account.cooldown_until = until
        account.bonus_minutes = bonus
        await db.flush()
    return Balance(True, False, limit + bonus, used, held, until)


async def check(db, user_id, machine, minutes, now, reservation=None):
    if machine.kind not in KINDS:
        return
    policy = await db.get(UsagePolicy, 1)
    if policy and reservation and reservation.created_at < policy.activated_at:
        return
    state = await balance(
        db, user_id, now, persist=True, exclude_reservation=reservation.id if reservation else None
    )
    if not state.enabled or state.unlimited:
        return
    if state.cooldown_until:
        raise DomainError(state.message)
    if minutes > state.available_minutes:
        raise DomainError(
            t.USAGE_INSUFFICIENT.format(minutes=hours_text(minutes)) + " " + state.message
        )


async def reconcile(db: AsyncSession, now: datetime | None = None):
    """Persist cycles and retry failed Telegram delivery on the next scheduler tick."""
    from app.bot import notify

    now = now or datetime.now(UTC)
    policy = await db.get(UsagePolicy, 1)
    if policy is None or not policy.enabled:
        return
    ids = list(await db.scalars(select(User.id).order_by(User.id)))
    await db.commit()
    for user_id in ids:
        state = await balance(db, user_id, now, persist=True)
        account = await db.get(UsageAccount, user_id)
        # State must survive a process restart even if Telegram is unavailable.
        await db.commit()
        if state.cooldown_until and account.notified_until != state.cooldown_until:
            if await notify.send_to_user(db, user_id, state.message):
                # The conditional update avoids marking a manually reset cycle delivered.
                from sqlalchemy import update

                await db.execute(
                    update(UsageAccount)
                    .where(
                        UsageAccount.user_id == user_id,
                        UsageAccount.cooldown_until == state.cooldown_until,
                    )
                    .values(notified_until=state.cooldown_until)
                )
                await db.commit()


async def form_limit(db, user, machine, now, reservation=None):
    if user is None or machine.kind not in KINDS:
        return None
    policy = await db.get(UsagePolicy, 1)
    if policy and reservation and reservation.created_at < policy.activated_at:
        return None
    state = await balance(
        db, user.id, now, exclude_reservation=reservation.id if reservation else None
    )
    return state if state.enabled and not state.unlimited else None


async def start_small_remainder_cooldown(db, user_id, now=None):
    """Explicit opt-in when the remainder cannot fit the minimum 15-minute session."""
    now = now or datetime.now(UTC)
    state = await balance(db, user_id, now, persist=True)
    if not state.can_forfeit_remainder:
        raise DomainError(t.USAGE_INVALID)
    policy = await db.get(UsagePolicy, 1)
    account = await db.get(UsageAccount, user_id)
    account.cooldown_until = now + timedelta(hours=policy.cooldown_hours)
    await db.flush()
