"""Часы работы мастерской: чтение и правка.

Бронировать можно только то время, когда мастерская открыта. До появления этого
модуля сетка расписания рисовала все 24 часа суток, и забронировать можно было
четыре утра — час, в который к машине всё равно никто не подойдёт.

Часы живут в базе (таблица `work_hours`, одна строка), а не в .env: меняет их
тот, кто отвечает за мастерскую, а не тот, у кого есть ssh на сервер. Цена — по
одному лишнему запросу на страницу расписания; выигрыш — правка часов из
админки, без перезапуска и без деплоя.

Арифметика здесь не живёт: что такое «влезает в рабочий день» и какие часы
показать в сетке, считает services/schedule.py — ему для этого не нужна база.
Здесь только хранение.
"""

from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from app import texts as t
from app.models import WorkHours
from app.services.errors import WorkHoursInvalid
from app.services.schedule import Hours

# Строку заводит миграция 0007, так что в живой базе она есть всегда. Значения
# отсюда — на случай, когда её всё же нет: экран на стене должен показать
# расписание, а не пятисотую.
DEFAULT = Hours(opens_at=time(8, 0), closes_at=time(20, 0))

ROW_ID = 1


async def get(db: AsyncSession) -> Hours:
    row = await db.get(WorkHours, ROW_ID)
    if row is None:
        return DEFAULT
    return Hours(opens_at=row.opens_at, closes_at=row.closes_at)


async def save(db: AsyncSession, opens_at: str, closes_at: str) -> Hours:
    """Записать часы из формы админки. Не коммитит — как и весь домен."""
    hours = Hours(opens_at=parse(opens_at), closes_at=parse(closes_at))
    if hours.closes_at <= hours.opens_at and not hours.round_the_clock:
        raise WorkHoursInvalid(t.ERR_WORK_HOURS_ORDER)

    row = await db.get(WorkHours, ROW_ID, with_for_update=True)
    if row is None:
        row = WorkHours(id=ROW_ID)
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
