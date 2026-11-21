from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(v) -> Optional[datetime]:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Trim nanoseconds -> microseconds for fromisoformat
    if "." in s:
        head, rest = s.split(".", 1)
        n = 0
        while n < len(rest) and rest[n].isdigit():
            n += 1
        frac = rest[:n]
        tz = rest[len(frac):]
        s = f"{head}.{frac[:6]}{tz}"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_date(v) -> Optional[date]:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def to_rfc3339(v) -> Optional[str]:
    """Accept date/datetime/str and return what Alpaca accepts."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        if not v.tzinfo:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def to_date_str(v) -> Optional[str]:
    d = parse_date(v)
    return d.isoformat() if d else None


def as_datetime(v) -> Optional[datetime]:
    """date/str/datetime -> aware datetime (dates become midnight UTC)."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    return parse_dt(v)
