from __future__ import annotations

from datetime import datetime, timezone, tzinfo


def local_datetime(value: str, *, target_tz: tzinfo | None = None) -> datetime | None:
    """Parse a stored timestamp and convert it to the requested local timezone."""

    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.removesuffix("Z") + ("+00:00" if text.endswith("Z") else ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(target_tz)


def format_local_timestamp(
    value: str,
    *,
    format: str = "%Y-%m-%d %H:%M:%S",
    target_tz: tzinfo | None = None,
) -> str:
    """Format UTC/ISO timestamps locally, preserving malformed historical text."""

    parsed = local_datetime(value, target_tz=target_tz)
    return parsed.strftime(format) if parsed is not None else value


def local_now_text(*, format: str = "%H:%M:%S", target_tz: tzinfo | None = None) -> str:
    """Return the current time using the same local display policy."""

    return datetime.now(timezone.utc).astimezone(target_tz).strftime(format)
