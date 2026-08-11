"""Работа со временем с учётом ночной паузы.

Правило 6 из PLAN.md: окно на подтверждение предложения из очереди не тикает
ночью. Принтер, освободившийся в 03:40, должен ждать человека до утра, иначе
ночные печати систематически опустошают очередь впустую.

Модуль намеренно не импортирует настройки: границы ночи передаются параметрами,
чтобы функции были чистыми и тестировались без окружения и без БД.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_NIGHT_START = time(23, 0)
DEFAULT_NIGHT_END = time(8, 0)

_MAX_ITERATIONS = 400  # предохранитель от бесконечного цикла при кривом конфиге


def is_night(
    moment: datetime,
    night_start: time = DEFAULT_NIGHT_START,
    night_end: time = DEFAULT_NIGHT_END,
) -> bool:
    """Попадает ли момент в ночное окно. Окно может пересекать полночь."""
    if night_start == night_end:
        return False

    current = moment.time()
    if night_start < night_end:
        return night_start <= current < night_end
    return current >= night_start or current < night_end


def add_active_minutes(
    start: datetime,
    minutes: int,
    night_start: time = DEFAULT_NIGHT_START,
    night_end: time = DEFAULT_NIGHT_END,
    tz: ZoneInfo | None = None,
) -> datetime:
    """Прибавить `minutes` активных минут, пропуская ночное окно.

    Ночные минуты не расходуются: старт внутри ночи переносится на утро, а
    остаток, не уместившийся до начала ночи, досчитывается со следующего утра.

    `start` должен быть aware-datetime. Если задан `tz`, расчёт идёт в этой зоне
    (границы ночи — локальные настенные часы), а результат возвращается в
    исходной зоне.
    """
    if minutes < 0:
        raise ValueError("minutes не может быть отрицательным")
    if start.tzinfo is None:
        raise ValueError("start должен быть aware-datetime")

    original_tz = start.tzinfo
    current = start.astimezone(tz) if tz else start

    if night_start == night_end:  # ночного окна нет — обычное сложение
        return (current + timedelta(minutes=minutes)).astimezone(original_tz)

    remaining = timedelta(minutes=minutes)

    for _ in range(_MAX_ITERATIONS):
        if remaining <= timedelta(0):
            break

        if is_night(current, night_start, night_end):
            current = _night_end_after(current, night_start, night_end)
            continue

        boundary = _next_night_start(current, night_start)
        available = boundary - current
        if available >= remaining:
            current = current + remaining
            remaining = timedelta(0)
            break

        remaining -= available
        current = boundary
    else:
        raise RuntimeError(
            f"не удалось посчитать срок за {_MAX_ITERATIONS} шагов: "
            f"проверь границы ночи {night_start}–{night_end}"
        )

    return current.astimezone(original_tz)


def _night_end_after(current: datetime, night_start: time, night_end: time) -> datetime:
    """Конец ночи, внутри которой находится `current`."""
    if night_start < night_end:  # окно внутри одних суток
        return _combine(current, current.date(), night_end)

    # окно пересекает полночь
    if current.time() >= night_start:
        return _combine(current, current.date() + timedelta(days=1), night_end)
    return _combine(current, current.date(), night_end)


def _next_night_start(current: datetime, night_start: time) -> datetime:
    """Ближайшее начало ночи строго после `current`."""
    today = _combine(current, current.date(), night_start)
    return today if today > current else today + timedelta(days=1)


def _combine(reference: datetime, day: date, moment: time) -> datetime:
    """Собрать datetime в той же зоне, что и reference.

    Настенное время: при переводе часов граница ночи сдвигается вместе с ним,
    что для 23:00/08:00 безопасно — переходы DST приходятся на 02:00–04:00.
    """
    return datetime.combine(day, moment, tzinfo=reference.tzinfo)
