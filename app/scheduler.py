"""Планировщик.

Делает ровно одно: раз в минуту зовёт `reminders.reconcile`. Хранилища заданий
нет и не нужно — вся память системы лежит в таблицах, поэтому после рестарта
или суточного простоя первая же сверка догонит всё пропущенное.

`max_instances=1` и `coalesce=True`: если сверка почему-то затянулась, вторая
поверх неё не запустится, а пропущенные тики схлопнутся в один.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db import SessionLocal
from app.services import reminders

logger = logging.getLogger(__name__)


async def tick() -> None:
    try:
        async with SessionLocal() as db:
            await reminders.reconcile(db)
    except Exception:  # один упавший тик не должен ронять планировщик
        logger.exception("сверка состояния упала")


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=str(settings.zone))
    scheduler.add_job(
        tick,
        "interval",
        seconds=settings.reconcile_seconds,
        id="reconcile",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
