"""Basic crypto data (v1beta3, US location). Symbols like 'BTC/USD'."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ...errors import NotFoundError
from ...models import BarSeries, Quote, Snapshot, Trade
from ...timeutil import as_datetime, to_rfc3339, utcnow
from ...transport import Transport
from . import mapping as m
from ._util import check_timeframe

BASE = "/v1beta3/crypto/us"


def _syms(symbols) -> list[str]:
    if isinstance(symbols, str):
        symbols = symbols.split(",")
    out = []
    for s in symbols:
        s = s.strip().upper()
        if "/" not in s and s.endswith("USD") and len(s) > 3:
            s = s[:-3] + "/USD"
        out.append(s)
    return out


class AlpacaCryptoData:
    def __init__(self, transport: Transport):
        self.t = transport

    def _latest(self, kind: str, symbol: str, mapper):
        sym = _syms(symbol)[0]
        b = self.t.data("GET", f"{BASE}/latest/{kind}", params={"symbols": sym}) or {}
        d = (b.get(kind) or {}).get(sym)
        if not d:
            raise NotFoundError(f"no crypto {kind} for {sym}")
        return mapper(sym, d, "crypto")

    def latest_quote(self, symbol: str) -> Quote:
        return self._latest("quotes", symbol, m.quote)

    def latest_trade(self, symbol: str) -> Trade:
        return self._latest("trades", symbol, m.trade)

    def snapshot(self, symbol: str) -> Snapshot:
        sym = _syms(symbol)[0]
        b = self.t.data("GET", f"{BASE}/snapshots", params={"symbols": sym}) or {}
        d = (b.get("snapshots") or {}).get(sym)
        if not d:
            raise NotFoundError(f"no crypto snapshot for {sym}")
        return m.snapshot(sym, d, "crypto")

    def bars(self, symbols, timeframe: str = "1Hour", start=None, end=None, *, days: Optional[int] = None,
             limit: Optional[int] = None) -> BarSeries:
        check_timeframe(timeframe)
        syms = _syms(symbols)
        end_dt = as_datetime(end) or utcnow()
        start_dt = as_datetime(start) or end_dt - timedelta(days=days or 3)
        out = {s: [] for s in syms}
        token = None
        while True:
            b = self.t.data("GET", f"{BASE}/bars", params={"symbols": ",".join(syms), "timeframe": timeframe,
                            "start": to_rfc3339(start_dt), "end": to_rfc3339(end_dt), "limit": 10000,
                            "page_token": token}) or {}
            for s, rows in (b.get("bars") or {}).items():
                out.setdefault(s, []).extend(m.bar(s, r, "crypto") for r in rows or [])
            token = b.get("next_page_token")
            if not token or (limit and sum(map(len, out.values())) >= limit):
                break
        if limit:
            out = {s: v[:limit] for s, v in out.items()}
        return BarSeries(bars=out, timeframe=timeframe, feed="crypto", requested_feed="crypto")
