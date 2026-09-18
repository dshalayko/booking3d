"""Global time allowance, per-user overrides, and an immutable change log."""

import json
from datetime import UTC, datetime, timedelta

from fastapi import Form, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.models import UsageAccount, UsageAudit, UsagePolicy, User
from app.services import usage_limits
from app.services.errors import DomainError, UserNotFound

router = core.section_router()
SECTION = core.Section(
    slug="limits",
    title=t.UI["usage_title"],
    icon="rules",
    router=router,
    group=core.GROUP_SETUP,
)


def snapshot(item):
    if isinstance(item, UsagePolicy):
        keys = ("enabled", "limit_minutes", "cooldown_hours", "activated_at")
    else:
        keys = ("limit_minutes", "unlimited", "bonus_minutes", "cycle_start", "cooldown_until")
    return {key: str(getattr(item, key)) for key in keys}


def audit(db, actor, user_id, action, before, after, reason):
    db.add(
        UsageAudit(
            actor_id=actor.id,
            user_id=user_id,
            details=json.dumps(
                {
                    "action": action,
                    "before": before,
                    "after": after,
                    "reason": reason,
                },
                ensure_ascii=False,
            ),
        )
    )


@router.get("/limits")
async def page(request: Request, db: Db, user_id: int | None = None, flash: str = ""):
    policy = await db.get(UsagePolicy, 1)
    if policy is None:
        policy = UsagePolicy(enabled=False, limit_minutes=300, cooldown_hours=168)
    query = select(User).order_by(User.name)
    if user_id is not None:
        query = query.where(User.id == user_id)
    rows = []
    for person in await db.scalars(query):
        rows.append(
            (
                person,
                await db.get(UsageAccount, person.id),
                await usage_limits.balance(db, person.id),
            )
        )
    admin_user, target_user = aliased(User), aliased(User)
    events = (
        await db.execute(
            select(UsageAudit, admin_user.name, target_user.name)
            .outerjoin(admin_user, admin_user.id == UsageAudit.actor_id)
            .outerjoin(target_user, target_user.id == UsageAudit.user_id)
            .order_by(UsageAudit.id.desc())
            .limit(50)
        )
    ).all()
    history = []
    for event, admin_name, user_name in events:
        details = json.loads(event.details)
        history.append(
            {
                "created_at": event.created_at,
                "admin": admin_name or "—",
                "user": user_name or t.UI["usage_all"],
                "action": t.UI["usage_action_" + details["action"]],
                "reason": details["reason"],
                "changes": [
                    (t.UI["usage_field_" + key], value, details["after"].get(key))
                    for key, value in details["before"].items()
                    if value != details["after"].get(key)
                ],
            }
        )
    return core.render(
        request,
        SECTION,
        "admin/limits.html",
        {"policy": policy, "rows": rows, "history": history},
        flash,
    )


@router.post("/limits")
async def save(
    request: Request,
    db: Db,
    enabled: str = Form(""),
    limit_minutes: int = Form(...),
    cooldown_hours: int = Form(...),
    reason: str = Form(...),
) -> Response:
    if not 1 <= limit_minutes <= 525600 or not 1 <= cooldown_hours <= 8760 or not reason.strip():
        raise DomainError(t.USAGE_INVALID)
    actor = await core.acting_admin(db, request)
    now = datetime.now(UTC)
    # Same order as spending: user locks first. All users are settled under the
    # previous policy before a policy change, preserving already-started cooldowns.
    ids = list(await db.scalars(select(User.id).order_by(User.id).with_for_update()))
    for uid in ids:
        await usage_limits.balance(db, uid, now, persist=True)
    policy = await db.get(UsagePolicy, 1, with_for_update=True, populate_existing=True)
    if policy is None:
        policy = UsagePolicy(
            id=1, enabled=False, limit_minutes=300, cooldown_hours=168, activated_at=now
        )
        db.add(policy)
    before = snapshot(policy)
    was_enabled = policy.enabled
    policy.enabled = enabled == "on"
    policy.limit_minutes = limit_minutes
    policy.cooldown_hours = cooldown_hours
    if policy.enabled and not was_enabled:
        policy.activated_at = now
        for account in await db.scalars(select(UsageAccount)):
            account.cycle_start = now
            account.cooldown_until = None
            account.notified_until = None
            account.bonus_minutes = 0
    await db.flush()
    # Lowering the allowance takes effect now, never backdates a new cooldown.
    for uid in ids:
        account = await db.get(UsageAccount, uid)
        if account and not account.cooldown_until:
            state = await usage_limits.balance(db, uid, now)
            if state.cooldown_until:
                account.cooldown_until = now + timedelta(hours=cooldown_hours)
    audit(db, actor, None, "policy", before, snapshot(policy), reason.strip())
    await db.commit()
    return core.redirect("usage_saved", SECTION.path)


@router.post("/limits/users/{user_id}")
async def change_user(
    request: Request,
    db: Db,
    user_id: int,
    action: str = Form("settings"),
    mode: str = Form("default"),
    limit_minutes: int = Form(300),
    bonus_minutes: int = Form(0),
    reason: str = Form(...),
) -> Response:
    if (
        action not in {"settings", "bonus", "reset"}
        or mode not in {"default", "custom", "unlimited"}
        or not 1 <= limit_minutes <= 525600
        or not 0 <= bonus_minutes <= 525600
        or not reason.strip()
        or (action == "bonus" and bonus_minutes == 0)
    ):
        raise DomainError(t.USAGE_INVALID)
    actor = await core.acting_admin(db, request)
    person = await db.get(User, user_id, with_for_update=True)
    if person is None:
        raise UserNotFound(t.ERR_USER_NOT_FOUND)
    now = datetime.now(UTC)
    await usage_limits.balance(db, user_id, now, persist=True)
    account = await db.get(UsageAccount, user_id)
    policy = await db.get(UsagePolicy, 1)
    if account is None:
        account = UsageAccount(user_id=user_id, unlimited=False, bonus_minutes=0, cycle_start=now)
        db.add(account)
    before = snapshot(account)
    if action == "reset":
        account.cycle_start = now
        account.cooldown_until = None
        account.notified_until = None
        account.bonus_minutes = 0
    elif action == "bonus":
        if account.cooldown_until:
            # A grant during cooldown unlocks a fresh allowance; only the granted
            # minutes are available until they are consumed, then cooldown resumes.
            account.cooldown_until = None
            account.notified_until = None
        account.bonus_minutes += bonus_minutes
    else:
        was_unlimited = account.unlimited
        account.unlimited = mode == "unlimited"
        account.limit_minutes = limit_minutes if mode == "custom" else None
        if was_unlimited != account.unlimited:
            account.cycle_start = now
            account.cooldown_until = None
            account.notified_until = None
            account.bonus_minutes = 0
    await db.flush()
    if policy and policy.enabled and not account.cooldown_until:
        state = await usage_limits.balance(db, user_id, now)
        if state.cooldown_until:
            account.cooldown_until = now + timedelta(hours=policy.cooldown_hours)
    audit(db, actor, user_id, action, before, snapshot(account), reason.strip())
    await db.commit()
    return core.redirect("usage_saved", f"{SECTION.path}?user_id={user_id}")
