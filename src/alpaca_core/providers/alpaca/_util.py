from __future__ import annotations

import re
from typing import Sequence

from ...errors import ValidationError

TIMEFRAME_RE = re.compile(r"^(\d+)(Min|T|Hour|H|Day|D|Week|W|Month|M)$")


def norm_symbols(symbols) -> list[str]:
    if symbols is None:
        return []
    if isinstance(symbols, str):
        symbols = symbols.split(",")
    out = [s.strip().upper() for s in symbols if s and s.strip()]
    if not out:
        raise ValidationError("at least one symbol is required")
    return out


def check_timeframe(tf: str) -> str:
    if not TIMEFRAME_RE.match(tf or ""):
        raise ValidationError(f"bad timeframe {tf!r}; use e.g. 1Min, 5Min, 15Min, 1Hour, 1Day, 1Week, 1Month")
    return tf


def paginate(fetch, key: str, limit: int | None = None, max_pages: int = 200):
    """Call fetch(page_token) repeatedly, yielding pages' `key` content until done or limit reached."""
    token, pages = None, 0
    while True:
        body = fetch(token) or {}
        pages += 1
        yield body.get(key)
        token = body.get("next_page_token")
        if not token or pages >= max_pages:
            return
