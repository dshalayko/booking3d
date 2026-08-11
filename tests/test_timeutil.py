from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.timeutil import add_active_minutes, is_night

TZ = ZoneInfo("Europe/Nicosia")

NIGHT_START = time(23, 0)
NIGHT_END = time(8, 0)


def dt(day: int, hour: int, minute: int = 0) -> datetime:
    """Локальное время в августе 2026."""
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


class TestIsNight:
    def test_midday_is_not_night(self):
        assert is_night(dt(10, 12)) is False

    def test_after_night_start(self):
        assert is_night(dt(10, 23, 30)) is True

    def test_before_night_end(self):
        assert is_night(dt(10, 3)) is True

    def test_night_start_is_inclusive(self):
        assert is_night(dt(10, 23, 0)) is True

    def test_night_end_is_exclusive(self):
        assert is_night(dt(10, 8, 0)) is False

    def test_window_within_single_day(self):
        assert is_night(dt(10, 3), time(1, 0), time(6, 0)) is True
        assert is_night(dt(10, 12), time(1, 0), time(6, 0)) is False

    def test_empty_window_never_night(self):
        assert is_night(dt(10, 3), time(23, 0), time(23, 0)) is False


class TestAddActiveMinutes:
    def test_daytime_is_plain_addition(self):
        assert add_active_minutes(dt(10, 10), 30) == dt(10, 10, 30)

    def test_remainder_carries_to_morning(self):
        # 22:50 + 30 мин: 10 минут до ночи, остаток 20 досчитывается с 08:00
        assert add_active_minutes(dt(10, 22, 50), 30) == dt(11, 8, 20)

    def test_start_inside_night_waits_for_morning(self):
        assert add_active_minutes(dt(10, 3), 30) == dt(10, 8, 30)

    def test_start_after_midnight_boundary(self):
        assert add_active_minutes(dt(10, 23, 30), 15) == dt(11, 8, 15)

    def test_ending_exactly_at_night_start_does_not_jump(self):
        # ровно упирается в 23:00 — переносить на утро нечего
        assert add_active_minutes(dt(10, 22, 30), 30) == dt(10, 23, 0)

    def test_starting_exactly_at_night_start(self):
        assert add_active_minutes(dt(10, 23, 0), 10) == dt(11, 8, 10)

    def test_spans_one_night(self):
        # 20:00 + 10 часов: 3 часа до ночи, остаток 7 часов с 08:00
        assert add_active_minutes(dt(10, 20), 600) == dt(11, 15, 0)

    def test_spans_two_nights(self):
        # 09:00 + 2000 мин: 840 в первый день, 900 во второй, 260 в третий
        assert add_active_minutes(dt(10, 9), 2000) == dt(12, 12, 20)

    def test_zero_minutes_returns_start(self):
        assert add_active_minutes(dt(10, 22, 55), 0) == dt(10, 22, 55)

    def test_zero_minutes_inside_night_returns_start(self):
        # ноль минут не должен ничего переносить, даже ночью
        assert add_active_minutes(dt(10, 3), 0) == dt(10, 3)

    def test_empty_night_window_is_plain_addition(self):
        result = add_active_minutes(dt(10, 22, 50), 30, time(23, 0), time(23, 0))
        assert result == dt(10, 23, 20)

    def test_night_window_within_single_day(self):
        result = add_active_minutes(dt(10, 0, 30), 60, time(1, 0), time(6, 0))
        assert result == dt(10, 6, 30)

    def test_keeps_timezone_of_input(self):
        # вход в UTC, границы ночи считаются по локальной зоне, ответ снова в UTC
        start = datetime(2026, 8, 10, 19, 50, tzinfo=UTC)  # 22:50 в Никосии
        result = add_active_minutes(start, 30, NIGHT_START, NIGHT_END, tz=TZ)
        assert result.tzinfo is UTC
        assert result == datetime(2026, 8, 11, 5, 20, tzinfo=UTC)  # 08:20 в Никосии

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError, match="aware"):
            add_active_minutes(datetime(2026, 8, 10, 10, 0), 30)

    def test_negative_minutes_rejected(self):
        with pytest.raises(ValueError, match="отрицательным"):
            add_active_minutes(dt(10, 10), -5)

    @pytest.mark.parametrize("minutes", [1, 59, 60, 61, 600, 1440, 5000])
    def test_result_is_never_inside_night(self, minutes):
        # окно подтверждения не должно истекать ночью ни при какой длительности.
        # Ровно 23:00 допустимо: минуты кончились в момент начала ночи.
        local = add_active_minutes(dt(10, 22, 50), minutes).astimezone(TZ)
        assert local.time() == NIGHT_START or not is_night(local)

    @pytest.mark.parametrize("minutes", [1, 30, 100, 500])
    def test_monotonic(self, minutes):
        start = dt(10, 22, 50)
        assert add_active_minutes(start, minutes) < add_active_minutes(start, minutes + 1)

    def test_matches_plain_addition_when_fully_inside_day(self):
        start = dt(10, 9)
        assert add_active_minutes(start, 120) == start + timedelta(minutes=120)
