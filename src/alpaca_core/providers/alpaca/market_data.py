"""Stock market data on Alpaca with smart feed selection (IEX real-time, SIP for >15-min-old history)."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Iterable, Optional

from ...errors import NotFoundError, PlanLimitError, ValidationError
from ...models import Bar, BarSeries, CorporateAction, Mover, Movers, NewsItem, Quote, Snapshot, Trade
from ...timeutil import as_datetime, to_date_str, to_rfc3339, utcnow
from ...transport import Transport
from . import mapping as m
from ._util import check_timeframe, norm_symbols

log = logging.getLogger("alpaca_core.market_data")
FEEDS = {"auto", "iex", "sip", "delayed_sip", "otc", "boats", "overnight"}


class AlpacaMarketData:
    def __init__(self, transport: Transport):
        self.t = transport
        self.cfg = transport.config

    # ---------------------------------------------------------- feed policy
    def realtime_feed(self, feed: str = "auto") -> str:
        if feed not in FEEDS:
            raise ValidationError(f"unknown feed {feed!r}")
        if feed != "auto":
            return feed
        return "sip" if self.cfg.data_plan == "sip" else "iex"

    def _get_latest(self, path: str, symbols, feed: str) -> tuple[dict, str]:
        """GET a latest-style endpoint; if auto-selected SIP is refused, retry on IEX. Returns (body, feed_used)."""
        syms = norm_symbols(symbols)
        use = self.realtime_feed(feed)
        try:
            return self.t.data("GET", path, params={"symbols": ",".join(syms), "feed": use}) or {}, use
        except PlanLimitError:
            if feed != "auto" or use == "iex":
                raise
            log.warning("SIP refused for %s, falling back to IEX", path)
            return self.t.data("GET", path, params={"symbols": ",".join(syms), "feed": "iex"}) or {}, "iex"

    def _latest(self, path: str, key: str, symbols, feed: str, mapper) -> dict:
        body, use = self._get_latest(path, symbols, feed)
        return {s: mapper(s, d, use) for s, d in (body.get(key) or {}).items()}

    @staticmethod
    def _one(d: dict, symbol: str):
        sym = symbol.upper()
        if sym not in d:
            raise NotFoundError(f"no data for {sym}")
        return d[sym]

    # ---------------------------------------------------------- latest
    def latest_quotes(self, symbols, feed: str = "auto") -> dict[str, Quote]:
        return self._latest("/v2/stocks/quotes/latest", "quotes", symbols, feed, m.quote)

    def latest_quote(self, symbol: str, feed: str = "auto") -> Quote:
        return self._one(self.latest_quotes([symbol], feed), symbol)

    def latest_trades(self, symbols, feed: str = "auto") -> dict[str, Trade]:
        return self._latest("/v2/stocks/trades/latest", "trades", symbols, feed, m.trade)

    def latest_trade(self, symbol: str, feed: str = "auto") -> Trade:
        return self._one(self.latest_trades([symbol], feed), symbol)

    def latest_bars(self, symbols, feed: str = "auto") -> dict[str, Bar]:
        return self._latest("/v2/stocks/bars/latest", "bars", symbols, feed, m.bar)

    def latest_bar(self, symbol: str, feed: str = "auto") -> Bar:
        return self._one(self.latest_bars([symbol], feed), symbol)

    def snapshots(self, symbols, feed: str = "auto") -> dict[str, Snapshot]:
        body, use = self._get_latest("/v2/stocks/snapshots", symbols, feed)
        # Multi-symbol response is a bare {SYMBOL: {...}} map; tolerate a {"snapshots": {...}} wrapper too.
        if isinstance(body.get("snapshots"), dict):
            body = body["snapshots"]
        return {s: m.snapshot(s, d, use) for s, d in body.items() if isinstance(d, dict)}

    def snapshot(self, symbol: str, feed: str = "auto") -> Snapshot:
        return self._one(self.snapshots([symbol], feed), symbol)

    # ---------------------------------------------------------- historical
    def bars(self, symbols, timeframe: str = "1Day", start=None, end=None, *, days: Optional[int] = None,
             feed: str = "auto", limit: Optional[int] = None, last: Optional[int] = None,
             adjustment: str = "raw") -> BarSeries:
        """Historical bars with pagination.

        feed="auto": on the free plan use SIP (full market) with `end` clamped to now-15min; if SIP is
        refused, fall back to IEX and flag `fallback=True`. On data_plan="sip" use SIP unclamped.
        `days` = calendar-day lookback when `start` is omitted; `last` trims to the last N bars/symbol.
        """
        syms = norm_symbols(symbols)
        check_timeframe(timeframe)
        if feed not in FEEDS:
            raise ValidationError(f"unknown feed {feed!r}")
        now = utcnow()
        end_dt = as_datetime(end) or now
        start_dt = as_datetime(start) or (end_dt - timedelta(days=days if days else (max(last or 0, 1) * 2 + 7 if "Day" in timeframe or timeframe.endswith("D") else 3)))
        cutoff = now - timedelta(minutes=self.cfg.sip_delay_minutes)

        use, clamped, note = feed, False, None
        if feed == "auto":
            if self.cfg.data_plan == "sip":
                use = "sip"
            elif start_dt >= cutoff:
                use, note = "iex", "window is entirely inside the SIP delay; used IEX"
            else:
                use = "sip"
                if end_dt > cutoff:
                    end_dt, clamped = cutoff, True
                    note = f"end clamped to now-{self.cfg.sip_delay_minutes}m for SIP (free plan)"
        fallback = False
        try:
            data = self._fetch_bars(syms, timeframe, start_dt, end_dt, use, limit, adjustment)
        except PlanLimitError:
            if feed != "auto" or use == "iex":
                raise
            log.warning("SIP bars refused; falling back to IEX")
            end_dt = as_datetime(end) or now
            use, fallback, clamped = "iex", True, False
            note = "SIP refused by data plan; fell back to IEX (partial-market volume)"
            data = self._fetch_bars(syms, timeframe, start_dt, end_dt, use, limit, adjustment)
        if last:
            data = {s: b[-last:] for s, b in data.items()}
        return BarSeries(bars=data, timeframe=timeframe, feed=use, requested_feed=feed, fallback=fallback,
                         end_clamped=clamped, note=note)

    def _fetch_bars(self, syms, timeframe, start, end, feed, limit, adjustment) -> dict[str, list[Bar]]:
        out: dict[str, list[Bar]] = {s: [] for s in syms}
        token, total = None, 0
        while True:
            params = {"symbols": ",".join(syms), "timeframe": timeframe, "start": to_rfc3339(start),
                      "end": to_rfc3339(end), "feed": feed, "adjustment": adjustment, "limit": 10000,
                      "sort": "asc", "page_token": token}
            body = self.t.data("GET", "/v2/stocks/bars", params=params) or {}
            for s, rows in (body.get("bars") or {}).items():
                for r in rows or []:
                    out.setdefault(s, []).append(m.bar(s, r, feed))
                    total += 1
            token = body.get("next_page_token")
            if not token or (limit and total >= limit):
                break
        if limit:
            out = {s: b[:limit] for s, b in out.items()}
        return out

    # ---------------------------------------------------------- screeners
    def movers(self, top: int = 10) -> Movers:
        b = self.t.data("GET", "/v1beta1/screener/stocks/movers", params={"top": top}) or {}
        return Movers(gainers=[m.mover(x) for x in b.get("gainers") or []],
                      losers=[m.mover(x) for x in b.get("losers") or []], last_updated=b.get("last_updated"))

    def most_actives(self, top: int = 10, by: str = "volume") -> list[Mover]:
        if by not in ("volume", "trades"):
            raise ValidationError("by must be 'volume' or 'trades'")
        b = self.t.data("GET", "/v1beta1/screener/stocks/most-actives", params={"top": top, "by": by}) or {}
        return [m.mover(x) for x in b.get("most_actives") or []]

    # ---------------------------------------------------------- news
    def news(self, symbols=None, limit: int = 20, start=None, end=None, include_content: bool = False) -> list[NewsItem]:
        syms = norm_symbols(symbols) if symbols else []
        out: list[NewsItem] = []
        token = None
        while len(out) < limit:
            params = {"symbols": ",".join(syms) or None, "limit": min(50, limit - len(out)),
                      "start": to_rfc3339(start), "end": to_rfc3339(end), "sort": "desc",
                      "include_content": str(include_content).lower(), "page_token": token}
            b = self.t.data("GET", "/v1beta1/news", params=params) or {}
            out += [m.news(x) for x in b.get("news") or []]
            token = b.get("next_page_token")
            if not token:
                break
        return out[:limit]

    # ---------------------------------------------------------- corporate actions
    def corporate_actions(self, symbols=None, types: Iterable[str] | None = None, start=None, end=None) -> list[CorporateAction]:
        """Defaults to a window of 90 days back through 90 days ahead (catches announced splits)."""
        today = utcnow().date()
        params = {"symbols": ",".join(norm_symbols(symbols)) if symbols else None,
                  "types": ",".join(types) if types else None,
                  "start": to_date_str(start) or (today - timedelta(days=90)).isoformat(),
                  "end": to_date_str(end) or (today + timedelta(days=90)).isoformat(),
                  "limit": 1000, "sort": "desc"}
        out: list[CorporateAction] = []
        token = None
        while True:
            b = self.t.data("GET", "/v1/corporate-actions", params={**params, "page_token": token}) or {}
            for kind, rows in (b.get("corporate_actions") or {}).items():
                out += [m.corporate_action(kind, r) for r in rows or []]
            token = b.get("next_page_token")
            if not token:
                break
        return out

    def reverse_splits(self, symbols=None, start=None, end=None) -> list[CorporateAction]:
        return self.corporate_actions(symbols, ["reverse_split"], start, end)
