"""Edit and export the English translation without changing deployed files."""

import json
from math import ceil
from urllib.parse import quote_plus

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.services import text_overrides

router = core.section_router()
PAGE_SIZE = 40

SECTION = core.Section(
    slug="texts",
    title=t.UI["admin_tab_texts"],
    heading=t.UI["admin_texts_title"],
    icon="language",
    router=router,
    group=core.GROUP_SETUP,
)


async def _page_context(db: Db, query: str, page: int) -> dict:
    overrides = await text_overrides.override_map(db)
    needle = query.casefold().strip()
    items = []
    for entry in text_overrides.entries():
        value = overrides.get(entry.key, entry.default)
        if needle and needle not in entry.key.casefold() and needle not in value.casefold():
            continue
        items.append(
            {
                "key": entry.key,
                "section": entry.section,
                "value": value,
                "default": entry.default,
                "changed": entry.key in overrides,
                "placeholders": sorted(text_overrides.placeholders(entry.default)),
            }
        )
    items.sort(key=lambda item: (item["section"], item["key"]))
    pages = max(1, ceil(len(items) / PAGE_SIZE))
    page = min(max(page, 1), pages)
    start = (page - 1) * PAGE_SIZE
    return {
        "items": items[start : start + PAGE_SIZE],
        "query": query.strip(),
        "query_encoded": quote_plus(query.strip()),
        "page": page,
        "pages": pages,
        "total": len(items),
    }


@router.get("/texts", response_class=HTMLResponse)
async def page(request: Request, db: Db, q: str = "", page: int = 1, flash: str = "") -> Response:
    return core.render(
        request,
        SECTION,
        "admin/texts.html",
        await _page_context(db, q, page),
        flash,
    )


@router.get("/texts/export")
async def export(db: Db) -> Response:
    overrides = await text_overrides.override_map(db)
    data = {
        entry.key: overrides.get(entry.key, entry.default)
        for entry in text_overrides.entries()
    }
    return Response(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="booking-en.json"'},
    )


@router.post("/texts")
async def save(
    db: Db,
    key: str = Form(...),
    value: str = Form(...),
    q: str = Form(""),
    page: int = Form(1),
) -> Response:
    try:
        await text_overrides.save(db, key, value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await db.commit()
    return core.redirect("text_saved", f"{SECTION.path}?q={quote_plus(q)}&page={page}")


@router.post("/texts/reset")
async def reset(
    db: Db,
    key: str = Form(...),
    q: str = Form(""),
    page: int = Form(1),
) -> Response:
    try:
        await text_overrides.reset(db, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await db.commit()
    return core.redirect("text_reset", f"{SECTION.path}?q={quote_plus(q)}&page={page}")
