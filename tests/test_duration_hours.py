from datetime import UTC, datetime

import pytest

from app.config import settings
from app.services.durations import hours_text, hours_value, minutes_from_hours
from app.services.schedule import duration_options


@pytest.mark.parametrize("minutes", [1, 2, 15, 30, 59, 60, 299, 300, 525600])
def test_existing_limits_round_trip_without_changing_storage(minutes):
    assert minutes_from_hours(hours_value(minutes)) == minutes


def test_hours_use_interface_locale_and_nonzero_small_usage(monkeypatch):
    monkeypatch.setattr(settings, "ui_lang", "en")
    assert hours_text(30) == "0.5"
    assert hours_text(299) == "4.98"
    assert hours_text(0.1) == "<0.01"
    monkeypatch.setattr(settings, "ui_lang", "ru")
    assert hours_text(30) == "0,5"


def test_morning_button_states_half_hour_and_end_time():
    # 08:30 in the test zone Europe/Nicosia.
    start = datetime(2026, 8, 10, 5, 30, tzinfo=UTC)
    option = next(o for o in duration_options(start) if o.label.startswith("до утра"))
    assert option.minutes == 30
    assert "09:00" in option.detail and "0,5 ч" in option.detail


def test_night_option_never_silently_truncates_to_quota():
    # 22:00: eleven hours until morning, but only five are available.
    start = datetime(2026, 8, 10, 19, tzinfo=UTC)
    assert not any(
        o.label.startswith("до утра") for o in duration_options(start, limit_minutes=300)
    )
