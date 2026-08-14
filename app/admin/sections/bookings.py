"""«Брони» — окна, забронированные на будущее.

Своя страница, а не блок «Сводки»: брони не видно ни на плитках, ни в очереди,
а снимать зависшую бронь иначе пришлось бы в psql. Список длинный и растёт со
временем — на оперативной странице он оттеснял бы вниз то, ради чего туда
заходят.
"""

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, Response

from app import texts as t
from app.admin import core
from app.api.deps import Db
from app.bot import notify, texts
from app.services import reservations as reservations_svc

router = core.section_router()

SECTION = core.Section(
    slug="bookings",
    title=t.UI["admin_bookings"],
    icon="calendar",
    router=router,
    group=core.GROUP_NOW,
)


@router.get("/bookings", response_class=HTMLResponse)
async def page(request: Request, db: Db, flash: str = "") -> Response:
    return core.render(
        request,
        SECTION,
        "admin/bookings.html",
        {"bookings": await reservations_svc.booked_ahead(db)},
        flash,
    )


@router.post("/bookings/{reservation_id}/cancel")
async def cancel(db: Db, reservation_id: int, reason: str = Form("")) -> Response:
    """Снять чужую бронь. Человек узнаёт об этом сообщением — своё окно он
    считал занятым и мог планировать вокруг него."""
    admin = await core.acting_admin(db)
    result = await reservations_svc.cancel(
        db, admin, reservation_id, reason=reason.strip() or None
    )
    await db.commit()

    if result.user_id != admin.id:
        await notify.send_to_user(
            db,
            result.user_id,
            texts.booking_cancelled_by_admin(result.machine_name, result.starts_at),
        )
    await notify.announce_offers(db, result.offers)
    return core.redirect("booking_cancelled", SECTION.path)
