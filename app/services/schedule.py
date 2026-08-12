"""Сетка календаря и длительности — арифметика времени без базы.

Здесь живёт то, что раньше было одной функцией `duration_options` в api/kiosk.py:
кнопки «1 ч / 2 ч / … / до утра». С появлением брон та же арифметика понадобилась
в трёх местах — экран занятия, экран бронирования и проверка в домене, — и
разошлась бы на первой же правке «до утра».

Модуль намеренно ничего не знает про базу: сюда приходят моменты времени и
границы, отсюда уходят списки вариантов. Что из этих вариантов реально свободно,
считает services/reservations.py, у которого есть сессия БД.

Всё считается в местной зоне (`settings.zone`), а наружу отдаётся в UTC: сетка
должна ложиться на часы, которые человек видит на стене, а не на UTC-полночь.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app import texts as t
from app.config import settings

# Границы длительности одной работы — общие для «занять сейчас» и для брони:
# машина не знает, чем её заняли, и 15 минут остаются 15 минутами в обоих
# случаях. Лежат здесь, а не в services/machines.py, потому что нужны и там, и
# в services/reservations.py, а тот от machines не зависит (иначе импорты
# замкнутся в кольцо).
MIN_DURATION_MINUTES = 15
MAX_DURATION_MINUTES = 48 * 60


@dataclass(frozen=True)
class DayOption:
    """Плитка в полосе дней над расписанием."""

    day: date
    iso: str
    label: str
    weekday: str
    is_today: bool


@dataclass(frozen=True)
class DurationOption:
    minutes: int
    label: str


def slots_per_day() -> int:
    return 24 * 60 // settings.reservation_slot_minutes


def local(moment: datetime) -> datetime:
    return moment.astimezone(settings.zone)


def align(moment: datetime) -> datetime:
    """Ближайшая граница сетки не позже `moment`.

    Выравнивание по местному времени, а не по UTC: в зоне со смещением :30
    (Индия, Непал) UTC-округление дало бы сетку из получасий, съехавшую от часов
    на стене.
    """
    point = local(moment)
    step = settings.reservation_slot_minutes
    minutes = (point.hour * 60 + point.minute) // step * step
    start_of_day = point.replace(hour=0, minute=0, second=0, microsecond=0)
    return (start_of_day + timedelta(minutes=minutes)).astimezone(UTC)


def is_aligned(moment: datetime) -> bool:
    """Начало по сетке. 14:00:30 — не начало слота, а результат опечатки в URL."""
    return align(moment) == moment.astimezone(UTC)


def day_of(moment: datetime) -> date:
    return local(moment).date()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Начало и конец местных суток в UTC."""
    start = datetime.combine(day, time(0, 0), tzinfo=settings.zone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def slot_starts(day: date) -> list[datetime]:
    """Все начала слотов этих суток, в UTC.

    Считается прибавлением часов к местной полуночи, поэтому в день перевода
    стрелок слотов честно окажется 23 или 25 — а не 24 с одним пропущенным.
    """
    start, end = day_bounds(day)
    points: list[datetime] = []
    point = start
    while point < end:
        points.append(point)
        point += timedelta(minutes=settings.reservation_slot_minutes)
    return points


def horizon_end(now: datetime) -> datetime:
    """Дальше этого момента бронировать нельзя."""
    return align(now) + timedelta(days=settings.reservation_horizon_days)


def day_options(now: datetime) -> list[DayOption]:
    """Полоса дней: сегодня и дальше до горизонта."""
    today = day_of(now)
    options = []
    for shift in range(settings.reservation_horizon_days):
        day = today + timedelta(days=shift)
        options.append(
            DayOption(
                day=day,
                iso=day.isoformat(),
                label=t.SCHEDULE_TODAY if shift == 0 else f"{day.day}.{day.month:02d}",
                weekday=t.WEEKDAY_SHORT[day.weekday()],
                is_today=shift == 0,
            )
        )
    return options


def morning_after(moment: datetime) -> datetime:
    """Ближайшее «утро» (`NIGHT_UNTIL`) строго позже момента.

    «До утра» — это не фиксированные 12 часов: работа, поставленная в 21:00,
    должна закончиться к открытию, а не в девять вечера следующего дня.
    """
    point = local(moment)
    morning = datetime.combine(point.date(), settings.night_until, tzinfo=point.tzinfo)
    if morning <= point:
        morning += timedelta(days=1)
    return morning.astimezone(UTC)


def minutes_until_morning(moment: datetime) -> int:
    return int((morning_after(moment) - moment).total_seconds() // 60)


def duration_options(
    start: datetime, limit_minutes: int | None = None, minimum: int = 0
) -> list[DurationOption]:
    """Кнопки длительности для старта в этот момент.

    `limit_minutes` — сколько машина свободна: до ближайшей чужой брони или до
    потолка. Варианты, которые в него не влезают, не показываются вовсе: кнопка,
    ведущая к отказу, хуже отсутствующей кнопки — человек уже ввёл PIN.
    """
    night = minutes_until_morning(start)
    options = [
        DurationOption(minutes=minutes, label=label)
        for minutes, label in t.DURATION_LABELS.items()
    ]
    # «До утра» показываем, только если это не тот же вариант, что уже есть
    # кнопкой: в 21:00 «до утра» и «12 ч» — одно и то же.
    if night not in t.DURATION_LABELS:
        options.append(DurationOption(minutes=night, label=t.DURATION_NIGHT))

    fitting = [
        option
        for option in options
        if option.minutes >= minimum
        and (limit_minutes is None or option.minutes <= limit_minutes)
    ]
    return sorted(fitting, key=lambda option: option.minutes)
