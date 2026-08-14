"""Часы работы помещения: чтение и правка.

Бронировать можно только то время, когда помещение открыто. До появления этого
модуля сетка расписания рисовала все 24 часа суток, и забронировать можно было
четыре утра — час, в который к машине всё равно никто не подойдёт.

Часы у каждого помещения свои: переговорная закрывается в шесть, а мастерская
работает до ночи, и одни часы на всё означали бы либо запертую до полуночи
переговорную, либо мастерскую, которую нельзя занять вечером.

Живут в базе (таблица `work_hours`, строка на помещение), а не в .env: меняет их
тот, кто отвечает за помещение, а не тот, у кого есть ssh на сервер. Цена — по
одному лишнему запросу на страницу расписания; выигрыш — правка часов из
админки, без перезапуска и без деплоя.

Строки может не быть: помещение заводится одним полем, и до первого сохранения
часов работает по `DEFAULT`. Пустая форма вместо расписания на свежей комнате
была бы хуже — человек не понял бы, чего от него хотят.

Арифметика здесь не живёт: что такое «влезает в рабочий день» и какие часы
показать в сетке, считает services/schedule.py — ему для этого не нужна база.
Здесь только хранение.
"""

from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.models import WorkHours
from app.services.errors import WorkHoursInvalid
from app.services.schedule import Hours

# По этим часам работает помещение, которому их ещё не задали. Значения те же,
# что миграция 0007 вписывала единственной строке: рабочий день коворкинга.
DEFAULT = Hours(opens_at=time(8, 0), closes_at=time(20, 0))


async def get(db: AsyncSession, room_id: int) -> Hours:
    row = await _row(db, room_id)
    if row is None:
        return DEFAULT
    return Hours(opens_at=row.opens_at, closes_at=row.closes_at)


async def by_room(db: AsyncSession, room_ids: list[int]) -> dict[int, Hours]:
    """Часы сразу нескольких помещений — для страницы админки и списка комнат.

    Одним запросом, а не `get` в цикле: на странице со всеми помещениями цикл
    означал бы запрос на каждую комнату ради двух значений времени.
    """
    rows = (
        await db.scalars(select(WorkHours).where(WorkHours.room_id.in_(room_ids)))
    ).all()
    found = {
        row.room_id: Hours(opens_at=row.opens_at, closes_at=row.closes_at) for row in rows
    }
    return {room_id: found.get(room_id, DEFAULT) for room_id in room_ids}


async def save(db: AsyncSession, room_id: int, opens_at: str, closes_at: str) -> Hours:
    """Записать часы помещения из формы админки. Не коммитит — как и весь домен."""
    hours = Hours(opens_at=parse(opens_at), closes_at=parse(closes_at))
    if hours.closes_at <= hours.opens_at and not hours.round_the_clock:
        raise WorkHoursInvalid(t.ERR_WORK_HOURS_ORDER)

    row = await _row(db, room_id, lock=True)
    if row is None:
        row = WorkHours(room_id=room_id)
        db.add(row)
    row.opens_at = hours.opens_at
    row.closes_at = hours.closes_at
    await db.flush()
    return hours


def parse(value: str) -> time:
    """«08:00» из формы. `<input type="time">` шлёт именно такую строку.

    Разбор строгий: браузер без поддержки типа отдаёт то, что человек набрал
    руками, и «8 утра» лучше отклонить с внятным текстом, чем понять как 08:00
    и однажды понять как-нибудь иначе.
    """
    try:
        return time.fromisoformat((value or "").strip())
    except ValueError as exc:
        raise WorkHoursInvalid(t.ERR_WORK_HOURS_FORMAT.format(value=value)) from exc


async def _row(db: AsyncSession, room_id: int, lock: bool = False) -> WorkHours | None:
    query = select(WorkHours).where(WorkHours.room_id == room_id)
    if lock:
        query = query.with_for_update()
    return await db.scalar(query)
