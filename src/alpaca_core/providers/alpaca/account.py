from __future__ import annotations

from typing import Iterable

from ...models import Account, Activity, CalendarDay, Clock, PortfolioHistory, Position
from ...timeutil import to_date_str, to_rfc3339
from ...transport import Transport
from . import mapping as m


def path_symbol(sym: str) -> str:
    return sym.upper().replace("/", "")


class AlpacaAccount:
    def __init__(self, transport: Transport):
        self.t = transport

    def account(self) -> Account:
        return m.account(self.t.trading("GET", "/v2/account"), self.t.config.mode)

    def positions(self) -> list[Position]:
        return [m.position(x) for x in self.t.trading("GET", "/v2/positions") or []]

    def position(self, symbol: str) -> Position:
        return m.position(self.t.trading("GET", f"/v2/positions/{path_symbol(symbol)}"))

    def activities(self, activity_types: Iterable[str] | None = None, after=None, until=None,
                   page_size: int = 100) -> list[Activity]:
        params = {"activity_types": ",".join(activity_types) if activity_types else None,
                  "after": to_rfc3339(after), "until": to_rfc3339(until), "page_size": page_size,
                  "direction": "desc"}
        return [m.activity(x) for x in self.t.trading("GET", "/v2/account/activities", params=params) or []]

    def portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> PortfolioHistory:
        return m.portfolio_history(self.t.trading("GET", "/v2/account/portfolio/history",
                                                  params={"period": period, "timeframe": timeframe}) or {})

    def clock(self) -> Clock:
        return m.clock(self.t.trading("GET", "/v2/clock"))

    def calendar(self, start=None, end=None) -> list[CalendarDay]:
        b = self.t.trading("GET", "/v2/calendar", params={"start": to_date_str(start), "end": to_date_str(end)})
        return [m.calendar_day(x) for x in b or []]
