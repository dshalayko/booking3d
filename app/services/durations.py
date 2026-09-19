"""Display durations in hours; retain minute precision in storage and APIs."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.config import settings


def hours_value(minutes: int | float | Decimal) -> str:
    value = Decimal(str(minutes)) / 60
    return (
        format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f").rstrip("0").rstrip(".")
        or "0"
    )


def hours_text(minutes: int | float | Decimal) -> str:
    value = hours_value(minutes)
    if value == "0" and 0 < abs(minutes):
        value = "<0.01"
    return value.replace(".", ",") if settings.ui_lang == "ru" else value


def minutes_from_hours(value: str) -> int:
    try:
        hours = Decimal(value.strip().replace(",", "."))
        if not hours.is_finite() or not 0 <= hours <= 8760:
            raise ValueError("hours out of range")
        return int((hours * 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("invalid hours") from exc
