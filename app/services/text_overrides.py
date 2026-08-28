"""Database-backed edits of the English translation catalogue.

The Python module stays untouched: production deploys replace the container,
and writing into ``en.py`` would both lose edits and make the checkout dirty.
Instead, stable leaf keys are derived from it and only changed values are kept
in Postgres.  Values are applied to the already imported dictionaries in
place, which also updates Jinja globals without restarting the app.
"""

import sys
from dataclasses import dataclass
from string import Formatter

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as active_texts
from app.models import TextOverride
from app.texts import en

MAX_VALUE_LENGTH = 10_000
# These are runtime formatting settings, not words for a linguist.  Editing one
# as prose would make strftime fail or change the document language metadata.
TECHNICAL_ROOTS = {"LANG", "TIME_FORMAT", "DATETIME_FORMAT"}


@dataclass(frozen=True)
class TextEntry:
    key: str
    section: str
    default: str
    root: str
    child: object | None = None
    index: int | None = None


def _child_name(value: object) -> str:
    # StrEnum.__str__ already returns its wire value; integers cover durations.
    return str(value)


def _build_catalog() -> dict[str, TextEntry]:
    result: dict[str, TextEntry] = {}
    for root, value in vars(en).items():
        if not root.isupper() or root.startswith("_") or root in TECHNICAL_ROOTS:
            continue
        if isinstance(value, str):
            result[root] = TextEntry(root, root, value, root)
        elif isinstance(value, dict):
            for child, text in value.items():
                if not isinstance(text, str):
                    continue
                key = f"{root}.{_child_name(child)}"
                if key in result:
                    raise RuntimeError(f"duplicate translation key: {key}")
                result[key] = TextEntry(key, root, text, root, child=child)
        elif isinstance(value, tuple):
            for index, text in enumerate(value):
                if isinstance(text, str):
                    key = f"{root}.{index}"
                    result[key] = TextEntry(key, root, text, root, index=index)
    return result


CATALOG = _build_catalog()


def entries() -> list[TextEntry]:
    return list(CATALOG.values())


def placeholders(value: str) -> set[str]:
    try:
        return {
            field.split(".", 1)[0].split("[", 1)[0]
            for _, field, _, _ in Formatter().parse(value)
            if field is not None
        }
    except ValueError as exc:
        raise ValueError(active_texts.ERR_TEXT_BRACES) from exc


def validate(key: str, value: str) -> TextEntry:
    entry = CATALOG.get(key)
    if entry is None:
        raise ValueError(active_texts.ERR_TEXT_UNKNOWN.format(key=key))
    if len(value) > MAX_VALUE_LENGTH:
        raise ValueError(active_texts.ERR_TEXT_LONG.format(limit=MAX_VALUE_LENGTH))
    expected = placeholders(entry.default)
    actual = placeholders(value)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "—"
        extra = ", ".join(sorted(actual - expected)) or "—"
        raise ValueError(active_texts.ERR_TEXT_PLACEHOLDERS.format(missing=missing, extra=extra))
    return entry


def _set(module, entry: TextEntry, value: str) -> None:
    current = getattr(module, entry.root)
    if entry.child is not None:
        current[entry.child] = value
    elif entry.index is not None:
        changed = list(current)
        changed[entry.index] = value
        setattr(module, entry.root, tuple(changed))
    else:
        setattr(module, entry.root, value)


def apply(key: str, value: str) -> None:
    entry = validate(key, value)
    _set(en, entry, value)
    if active_texts.LANG == "en":
        # Dicts are shared by star import, while strings and tuples need a new
        # attribute on the selected-language module.
        _set(active_texts, entry, value)

        # These two legacy aliases are evaluated at import time rather than
        # read from app.texts for every message.
        bot_texts = sys.modules.get("app.bot.texts")
        if bot_texts is not None and key == "BOT_HELP":
            bot_texts.HELP = value

        bot_module = sys.modules.get("app.bot.bot")
        if bot_module is not None and key.startswith("BOT_COMMAND_DESCRIPTIONS."):
            command_type = type(bot_module.BOT_COMMANDS[0])
            bot_module.BOT_COMMANDS = [
                command_type(command=command, description=description)
                for command, description in active_texts.BOT_COMMAND_DESCRIPTIONS.items()
            ]

        # Section labels are frozen value objects created while modules import.
        # Refresh the few cached labels; ordinary template text lives in the UI
        # dict and was already updated in place above.
        if entry.root == "UI" and isinstance(entry.child, str):
            admin_module = sys.modules.get("app.admin")
            if admin_module is not None and hasattr(admin_module, "SECTIONS"):
                labels = {
                    "": ("admin_tab_summary", None),
                    "bookings": ("admin_bookings", None),
                    "feedback": ("admin_feedback", None),
                    "people": ("admin_nav_people", None),
                    "log": ("admin_nav_events", "admin_events"),
                    "rooms": ("admin_tab_rooms", None),
                    "machines": ("admin_tab_machines", None),
                    "hours": ("admin_tab_hours", None),
                    "rules": ("admin_tab_rules", None),
                    "texts": ("admin_tab_texts", "admin_texts_title"),
                }
                for section in admin_module.SECTIONS:
                    title_key, heading_key = labels[section.slug]
                    if entry.child == title_key:
                        object.__setattr__(section, "title", value)
                    if entry.child == heading_key:
                        object.__setattr__(section, "heading", value)

        if key == "API_TITLE":
            main_module = sys.modules.get("app.main")
            if main_module is not None and hasattr(main_module, "app"):
                main_module.app.title = value


def reset_runtime(key: str) -> None:
    apply(key, CATALOG[key].default)


async def override_map(db: AsyncSession) -> dict[str, str]:
    rows = (await db.scalars(select(TextOverride).order_by(TextOverride.key))).all()
    return {row.key: row.value for row in rows if row.key in CATALOG}


async def load_and_apply(db: AsyncSession) -> None:
    for key, value in (await override_map(db)).items():
        try:
            apply(key, value)
        except ValueError:
            # A source update may have changed placeholders.  Keep the old row
            # for correction in admin, but never let it break application boot.
            continue


async def save(db: AsyncSession, key: str, value: str) -> None:
    entry = validate(key, value)
    if value == entry.default:
        await db.execute(delete(TextOverride).where(TextOverride.key == key))
        reset_runtime(key)
        return
    row = await db.get(TextOverride, key)
    if row is None:
        db.add(TextOverride(key=key, value=value))
    else:
        row.value = value
    apply(key, value)


async def reset(db: AsyncSession, key: str) -> None:
    if key not in CATALOG:
        raise ValueError(active_texts.ERR_TEXT_UNKNOWN.format(key=key))
    await db.execute(delete(TextOverride).where(TextOverride.key == key))
    reset_runtime(key)
