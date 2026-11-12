"""Options reference + market data (free plan: 'indicative' feed)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from ...errors import PlanLimitError, ValidationError
from ...models import OptionChain, OptionContract, Quote
from ...timeutil import to_date_str, utcnow
from zoneinfo import ZoneInfo


def _today_et() -> date:
    return utcnow().astimezone(ZoneInfo("America/New_York")).date()
from ...transport import Transport
from . import mapping as m
from ._util import norm_symbols


class AlpacaOptionsData:
    def __init__(self, transport: Transport):
        self.t = transport
        self.cfg = transport.config

    @property
    def feed(self) -> str:
        return self.cfg.options_feed

    def _type(self, type: Optional[str]) -> Optional[str]:
        if type is None:
            return None
        t = type.lower()
        if t in ("c", "call", "calls"):
            return "call"
        if t in ("p", "put", "puts"):
            return "put"
        raise ValidationError("type must be 'call' or 'put'")

    def contracts(self, underlying: str, expiration=None, *, expiration_gte=None, expiration_lte=None,
                  type: Optional[str] = None, strike_gte: Optional[float] = None, strike_lte: Optional[float] = None,
                  limit: Optional[int] = None) -> list[OptionContract]:
        params = {"underlying_symbols": underlying.upper(), "status": "active",
                  "expiration_date": to_date_str(expiration), "expiration_date_gte": to_date_str(expiration_gte),
                  "expiration_date_lte": to_date_str(expiration_lte), "type": self._type(type),
                  "strike_price_gte": strike_gte, "strike_price_lte": strike_lte, "limit": 10000}
        out: list[OptionContract] = []
        token = None
        while True:
            b = self.t.trading("GET", "/v2/options/contracts", params={**params, "page_token": token}) or {}
            out += [m.option_contract(x) for x in b.get("option_contracts") or []]
            token = b.get("next_page_token")
            if not token or (limit and len(out) >= limit):
                break
        return out[:limit] if limit else out

    def expirations(self, underlying: str, *, min_dte: int = 0, max_dte: Optional[int] = None) -> list[date]:
        today = _today_et()
        cs = self.contracts(underlying, expiration_gte=today + timedelta(days=min_dte),
                            expiration_lte=(today + timedelta(days=max_dte)) if max_dte is not None else None)
        return sorted({c.expiration for c in cs if c.expiration})

    def chain(self, underlying: str, expiration=None, *, expiration_gte=None, expiration_lte=None,
              type: Optional[str] = None,
              strike_gte: Optional[float] = None, strike_lte: Optional[float] = None,
              limit: Optional[int] = None) -> OptionChain:
        """Chain with quotes/greeks/IV from the snapshots endpoint."""
        # Snapshot endpoint still returns just-expired series; default to today onward (US/Eastern date).
        gte = None if expiration else to_date_str(expiration_gte) or _today_et().isoformat()
        params = {"feed": self.feed, "expiration_date": to_date_str(expiration), "expiration_date_gte": gte,
                  "expiration_date_lte": to_date_str(expiration_lte), "type": self._type(type),
                  "strike_price_gte": strike_gte, "strike_price_lte": strike_lte, "limit": 1000}
        out: list[OptionContract] = []
        token = None
        while True:
            b = self.t.data("GET", f"/v1beta1/options/snapshots/{underlying.upper()}",
                            params={**params, "page_token": token}) or {}
            out += [m.option_snapshot(s, d, self.feed) for s, d in (b.get("snapshots") or {}).items()]
            token = b.get("next_page_token")
            if not token or (limit and len(out) >= limit):
                break
        out.sort(key=lambda c: (c.expiration or date.max, c.strike or 0, c.type or ""))
        return OptionChain(underlying=underlying.upper(), contracts=out[:limit] if limit else out, feed=self.feed)

    def snapshots(self, symbols) -> dict[str, OptionContract]:
        syms = norm_symbols(symbols)
        out: dict[str, OptionContract] = {}
        for i in range(0, len(syms), 100):
            b = self.t.data("GET", "/v1beta1/options/snapshots",
                            params={"symbols": ",".join(syms[i:i + 100]), "feed": self.feed}) or {}
            out.update({s: m.option_snapshot(s, d, self.feed) for s, d in (b.get("snapshots") or {}).items()})
        return out

    def latest_quotes(self, symbols) -> dict[str, Quote]:
        syms = norm_symbols(symbols)
        b = self.t.data("GET", "/v1beta1/options/quotes/latest",
                        params={"symbols": ",".join(syms), "feed": self.feed}) or {}
        return {s: m.quote(s, d, self.feed) for s, d in (b.get("quotes") or {}).items()}
