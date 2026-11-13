from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))


def normalize_phone(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        return None
    return "+91" + digits


def normalize_date(value: str) -> str | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def normalize_decimal(value: str) -> str | None:
    cleaned = re.sub(r"^(?:₹|rs\.?|inr)\s*", "", value.strip(), flags=re.IGNORECASE)
    if not cleaned or not re.fullmatch(r"\d[\d,]*(?:\.\d+)?", cleaned):
        return None
    try:
        number = Decimal(cleaned.replace(",", ""))
        if not number.is_finite() or number < 0:
            return None
        return str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def normalize_value(field: dict[str, Any], raw: str) -> tuple[str | None, str | None]:
    """Return (normalized value, error code); an empty value is (None, None)."""
    value = raw.strip()
    if not value:
        return None, None
    kind = field["type"]
    if kind == "string":
        return value, None
    if kind == "phone":
        normalized = normalize_phone(value)
        return (normalized, None) if normalized else (None, "invalid_phone")
    if kind == "date":
        normalized = normalize_date(value)
        return (normalized, None) if normalized else (None, "invalid_date")
    if kind == "decimal":
        normalized = normalize_decimal(value)
        return (normalized, None) if normalized else (None, "invalid_decimal")
    if kind == "enum":
        choices = {str(choice).lower() for choice in field.get("values", [])}
        normalized = value.lower()
        return (normalized, None) if normalized in choices else (None, "invalid_enum")
    raise ValueError(f"Unsupported schema field type: {kind}")


def parse_timestamp(value: str) -> datetime | None:
    text = value.strip()
    try:
        if text.endswith("Z"):
            parsed = datetime.fromisoformat(text[:-1] + "+00:00")
        else:
            parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def ist_datetime(utc_iso: str) -> datetime:
    return datetime.fromisoformat(utc_iso).astimezone(IST)
