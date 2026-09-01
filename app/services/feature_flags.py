"""Переключатели экспериментальных возможностей из административной панели."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FeatureFlags

FLAGS_ID = 1


async def slicer_enabled(db: AsyncSession) -> bool:
    """Показывать и обслуживать расчёт STL.

    Отсутствующая строка означает «включено»: так приложение остаётся доступным
    во время первого запуска до заполнения singleton-строки миграцией.
    """
    flags = await db.get(FeatureFlags, FLAGS_ID)
    return True if flags is None else flags.slicer_enabled


async def save_slicer(db: AsyncSession, value: bool) -> FeatureFlags:
    flags = await db.get(FeatureFlags, FLAGS_ID, with_for_update=True)
    if flags is None:
        flags = FeatureFlags(id=FLAGS_ID, slicer_enabled=value)
        db.add(flags)
    else:
        flags.slicer_enabled = value
    await db.flush()
    return flags
