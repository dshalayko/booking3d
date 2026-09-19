import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.bot import notify
from app.config import settings
from app.models import Reservation, UsageAccount, UsageAudit, UsagePolicy
from app.services import booking_policy, machines, reservations, usage_limits
from app.services.errors import DomainError

NOW = datetime(2026, 9, 19, 10, tzinfo=UTC)


async def enable(db, minutes=300, hours=168, now=NOW):
    db.add(
        UsagePolicy(
            id=1, enabled=True, limit_minutes=minutes, cooldown_hours=hours, activated_at=now
        )
    )
    await db.commit()


async def test_existing_users_and_old_history(db, make_user, printers):
    user = await make_user()
    await machines.occupy(db, user, printers[0].id, 600, now=NOW - timedelta(days=1))
    await machines.release(db, user, printers[0].id, now=NOW - timedelta(hours=1))
    await enable(db)
    assert (await usage_limits.balance(db, user.id, NOW)).available_minutes == 300
    await machines.occupy(db, user, printers[0].id, 300, now=NOW)
    state = await usage_limits.balance(db, user.id, NOW + timedelta(hours=2))
    assert state.used_minutes == 120
    assert state.held_minutes == 180
    assert state.cooldown_until is None
    assert state.available_minutes == 0


async def test_exact_exhaustion_and_restore_after_downtime(db, make_user, printers):
    user = await make_user()
    await enable(db, hours=48)
    await machines.occupy(db, user, printers[0].id, 300, now=NOW)
    exhausted = NOW + timedelta(hours=5)
    state = await usage_limits.balance(db, user.id, exhausted + timedelta(hours=1), persist=True)
    assert state.cooldown_until == exhausted + timedelta(hours=48)
    with pytest.raises(DomainError, match="исчерпан"):
        await usage_limits.check(db, user.id, printers[1], 60, exhausted + timedelta(hours=47))
    state = await usage_limits.balance(db, user.id, exhausted + timedelta(hours=48), persist=True)
    assert state.available_minutes == 300
    assert state.cooldown_until is None
    assert state.used_minutes == 0  # done_wait is not billable


async def test_early_release_and_broken_return_unused_time(db, make_user, printers):
    user = await make_user(is_admin=True)
    await enable(db)
    await machines.occupy(db, user, printers[0].id, 300, now=NOW)
    await machines.release(db, user, printers[0].id, now=NOW + timedelta(minutes=45))
    assert (
        await usage_limits.balance(db, user.id, NOW + timedelta(hours=1))
    ).available_minutes == 255
    await machines.occupy(db, user, printers[0].id, 255, now=NOW + timedelta(hours=1))
    await machines.set_broken(db, user, printers[0].id, now=NOW + timedelta(hours=2))
    assert (
        await usage_limits.balance(db, user.id, NOW + timedelta(hours=3))
    ).available_minutes == 195


async def test_reservation_holds_cancel_returns_and_take_deduplicates(db, make_user, printers):
    user = await make_user()
    await enable(db)
    start = NOW + timedelta(hours=2)
    result = await reservations.book(db, user, printers[0].id, start, 300, now=NOW)
    state = await usage_limits.balance(db, user.id, NOW)
    assert state.held_minutes == 300 and state.cooldown_until is None
    await reservations.cancel(db, user, result.reservation_id, now=NOW)
    assert (await usage_limits.balance(db, user.id, NOW)).available_minutes == 300
    result = await reservations.book(db, user, printers[0].id, start, 300, now=NOW)
    await machines.occupy(db, user, printers[0].id, 300, now=start)
    state = await usage_limits.balance(db, user.id, start + timedelta(hours=1))
    assert state.used_minutes == 60 and state.held_minutes == 240


async def test_grandfathered_reservation_can_start_over_limit(db, make_user, printers):
    user = await make_user()
    start = NOW + timedelta(hours=2)
    result = await reservations.book(db, user, printers[0].id, start, 600, now=NOW)
    row = await db.get(Reservation, result.reservation_id)
    row.created_at = NOW - timedelta(days=1)
    await enable(db)
    assert (await usage_limits.balance(db, user.id, NOW)).available_minutes == 300
    await machines.occupy(db, user, printers[0].id, 600, now=start)
    assert (await usage_limits.balance(db, user.id, start + timedelta(hours=3))).used_minutes == 0


async def test_parallel_machine_requests_cannot_overspend(db, sessions, make_user, printers):
    user = await make_user()
    await enable(db, minutes=90)
    await booking_policy.save(db, True)
    await db.commit()

    async def start(machine_id):
        async with sessions() as session:
            try:
                await machines.occupy(session, user, machine_id, 60, now=NOW)
                await session.commit()
                return True
            except DomainError:
                return False

    results = await asyncio.gather(*(start(p.id) for p in printers))
    assert sorted(results) == [False, True]


async def test_parallel_consumption_exact_boundary(db, make_user, printers):
    user = await make_user()
    await enable(db, minutes=120)
    await booking_policy.save(db, True)
    await machines.occupy(db, user, printers[0].id, 60, now=NOW)
    await machines.occupy(db, user, printers[1].id, 60, now=NOW)
    state = await usage_limits.balance(db, user.id, NOW + timedelta(hours=2))
    assert state.cooldown_until == NOW + timedelta(hours=169)


async def test_notification_retry_and_no_repeat(db, make_user, printers):
    user = await make_user()
    await enable(db, minutes=60)
    await machines.occupy(db, user, printers[0].id, 60, now=NOW)
    await db.commit()
    calls = []

    async def sender(chat, text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("offline")

    notify.set_sender(sender)
    try:
        for _ in range(3):
            await usage_limits.reconcile(db, NOW + timedelta(hours=2))
        assert len(calls) == 2
        assert "исчерпан" in calls[-1]
    finally:
        notify.set_sender(None)


async def test_admin_settings_overrides_reset_and_audit(client, db, make_user):
    await make_user(is_admin=True)
    user = await make_user()
    await client.post("/admin/login", data={"secret": settings.admin_secret})
    response = await client.post(
        "/admin/limits",
        data={
            "enabled": "on",
            "limit_minutes": 300,
            "cooldown_hours": 72,
            "reason": "rollout",
        },
    )
    assert response.status_code == 303
    response = await client.get("/admin/limits")
    assert response.status_code == 200 and "Cooldown" in response.text
    for payload in [
        {"mode": "custom", "limit_minutes": 600},
        {"action": "bonus", "bonus_minutes": 120},
        {"action": "reset"},
        {"mode": "unlimited"},
    ]:
        response = await client.post(
            f"/admin/limits/users/{user.id}", data={**payload, "reason": "exception"}
        )
        assert response.status_code == 303
    assert (await usage_limits.balance(db, user.id)).unlimited
    assert len(list(await db.scalars(select(UsageAudit)))) == 5
    response = await client.post(
        "/admin/limits",
        data={
            "enabled": "on",
            "limit_minutes": 0,
            "cooldown_hours": -1,
            "reason": "bad",
        },
    )
    assert response.status_code == 400


async def test_cooldown_blocks_future_bookings_and_meeting_is_exempt(
    db, make_user, printers, meeting
):
    user = await make_user()
    await enable(db, minutes=60)
    await machines.occupy(db, user, printers[0].id, 60, now=NOW)
    await machines.release(db, user, printers[0].id, now=NOW + timedelta(hours=1))
    with pytest.raises(DomainError, match="исчерпан"):
        await reservations.book(
            db, user, printers[1].id, NOW + timedelta(days=10), 60, now=NOW + timedelta(hours=2)
        )
    await machines.occupy(db, user, meeting[1].id, 60, now=NOW + timedelta(hours=2))


async def test_remainder_opt_in_requires_no_reserved_work(db, make_user, printers):
    user = await make_user()
    await enable(db, minutes=60, hours=24)
    await machines.occupy(db, user, printers[0].id, 60, now=NOW)
    with pytest.raises(DomainError):
        await usage_limits.start_small_remainder_cooldown(db, user.id, NOW + timedelta(minutes=55))
    await machines.release(db, user, printers[0].id, now=NOW + timedelta(minutes=55))
    state = await usage_limits.balance(db, user.id, NOW + timedelta(minutes=55))
    assert state.available_minutes == 5 and state.can_forfeit_remainder
    await usage_limits.start_small_remainder_cooldown(db, user.id, NOW + timedelta(hours=1))
    state = await usage_limits.balance(db, user.id, NOW + timedelta(hours=2))
    assert state.cooldown_until == NOW + timedelta(hours=25)


async def test_manual_reset_preserves_remaining_active_reserve(client, db, make_user, printers):
    user = await make_user(is_admin=True)
    now = datetime.now(UTC)
    await enable(db, now=now - timedelta(hours=2))
    await machines.occupy(db, user, printers[0].id, 300, now=now - timedelta(hours=1))
    await db.commit()
    await client.post("/admin/login", data={"secret": settings.admin_secret})
    response = await client.post(
        f"/admin/limits/users/{user.id}", data={"action": "reset", "reason": "test"}
    )
    assert response.status_code == 303
    state = await usage_limits.balance(db, user.id)
    assert state.used_minutes == 0
    assert state.held_minutes == 240
    assert state.available_minutes == 60


async def test_personal_limit_bonus_and_frozen_cooldown(client, db, make_user, printers):
    user = await make_user(is_admin=True)
    now = datetime.now(UTC)
    await enable(db, minutes=60, hours=168, now=now - timedelta(hours=3))
    await machines.occupy(db, user, printers[0].id, 60, now=now - timedelta(hours=2))
    await machines.release(db, user, printers[0].id, now=now - timedelta(hours=1))
    state = await usage_limits.balance(db, user.id, now, persist=True)
    until = state.cooldown_until
    await db.commit()
    await client.post("/admin/login", data={"secret": settings.admin_secret})
    await client.post(
        "/admin/limits",
        data={
            "enabled": "on",
            "limit_minutes": 60,
            "cooldown_hours": 24,
            "reason": "change",
        },
    )
    assert (await usage_limits.balance(db, user.id)).cooldown_until == until
    await client.post(
        f"/admin/limits/users/{user.id}",
        data={"action": "bonus", "bonus_minutes": 30, "reason": "extra"},
    )
    state = await usage_limits.balance(db, user.id)
    assert state.available_minutes == 30 and state.cooldown_until is None
    await client.post(
        f"/admin/limits/users/{user.id}",
        data={"mode": "custom", "limit_minutes": 120, "reason": "custom"},
    )
    state = await usage_limits.balance(db, user.id)
    assert state.limit_minutes == 150 and state.available_minutes == 90


async def test_limits_admin_requires_auth(client, make_user):
    user = await make_user()
    assert (await client.get("/admin/limits")).status_code == 403
    assert (
        await client.post(
            "/admin/limits/users/" + str(user.id), data={"action": "reset", "reason": "forbidden"}
        )
    ).status_code == 403


async def test_miniapp_shows_balance_and_fitting_durations(
    client, db, make_user, printers, monkeypatch
):
    await make_user()
    monkeypatch.setattr(settings, "miniapp_open_access", True)
    await enable(db, minutes=80, now=datetime.now(UTC) - timedelta(minutes=1))
    await client.post("/app/session", data={"init_data": ""})
    response = await client.get("/app/")
    assert response.status_code == 200
    assert "usage-card" in response.text
    assert "Осталось" in response.text and "1,33 ч" in response.text
    assert "Исп." in response.text and "0 ч" in response.text
    assert "из 1,33 ч" in response.text
    response = await client.get(f"/app/occupy/{printers[0].id}")
    assert response.status_code == 200
    assert 'value="80"' in response.text
    assert 'value="120"' not in response.text


async def test_admin_accepts_hours_and_preserves_minute_storage(client, db, make_user):
    user = await make_user(is_admin=True)
    await client.post("/admin/login", data={"secret": settings.admin_secret})
    response = await client.post(
        "/admin/limits",
        data={
            "enabled": "on",
            "limit_hours": "6.5",
            "cooldown_hours": 48,
            "reason": "hours",
        },
    )
    assert response.status_code == 303
    assert (await db.get(UsagePolicy, 1)).limit_minutes == 390
    response = await client.post(
        f"/admin/limits/users/{user.id}",
        data={
            "mode": "custom",
            "limit_hours": "4,25",
            "reason": "personal",
        },
    )
    assert response.status_code == 303
    response = await client.post(
        f"/admin/limits/users/{user.id}",
        data={
            "action": "bonus",
            "bonus_hours": "0.5",
            "reason": "extra",
        },
    )
    assert response.status_code == 303
    state = await usage_limits.balance(db, user.id)
    assert state.limit_minutes == 285
    page = await client.get("/admin/limits")
    assert 'name="limit_hours"' in page.text and 'value="6.5"' in page.text
    assert 'name="bonus_hours"' in page.text
    assert "Лимит, ч: 6,5" in page.text or "Лимит, ч:" in page.text
    assert "мин." not in state.message


async def test_admin_limits_do_not_require_reason(client, db, make_user):
    await make_user(is_admin=True)
    user = await make_user()
    await client.post("/admin/login", data={"secret": settings.admin_secret})
    response = await client.post(
        "/admin/limits",
        data={"enabled": "on", "limit_hours": "5", "cooldown_hours": 168},
    )
    assert response.status_code == 303
    response = await client.post(
        f"/admin/limits/users/{user.id}",
        data={"mode": "custom", "limit_hours": "2"},
    )
    assert response.status_code == 303
    assert (await db.get(UsageAccount, user.id)).limit_minutes == 120
    response = await client.post(
        f"/admin/limits/users/{user.id}",
        data={"action": "bonus", "bonus_hours": "0.5"},
    )
    assert response.status_code == 303
    state = await usage_limits.balance(db, user.id)
    assert state.limit_minutes == 150
    events = list(await db.scalars(select(UsageAudit).order_by(UsageAudit.id)))
    assert len(events) == 3
    assert all(json.loads(event.details)["reason"] for event in events)
    page = await client.get(f"/admin/limits?user_id={user.id}")
    assert page.status_code == 200
    assert 'name="reason"' not in page.text
    assert "Reason for change" not in page.text
    assert 'data-usage-search' in page.text
    assert 'data-usage-search-empty' in page.text
    assert f'data-usage-user="{user.name.lower()}"' in page.text


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "8761", "abc", ""])
async def test_admin_rejects_invalid_hours(client, db, make_user, value):
    await make_user(is_admin=True)
    await enable(db)
    await client.post("/admin/login", data={"secret": settings.admin_secret})
    response = await client.post(
        "/admin/limits",
        data={
            "enabled": "on",
            "limit_hours": value,
            "cooldown_hours": 168,
            "reason": "invalid",
        },
    )
    assert response.status_code in (400, 422)
    assert (await db.get(UsagePolicy, 1)).limit_minutes == 300
