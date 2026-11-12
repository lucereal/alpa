from __future__ import annotations

from typing import Optional, Sequence

from ...models import Watchlist
from ...transport import Transport
from . import mapping as m


class AlpacaWatchlists:
    def __init__(self, transport: Transport):
        self.t = transport

    def list(self) -> list[Watchlist]:
        return [m.watchlist(x) for x in self.t.trading("GET", "/v2/watchlists") or []]

    def get(self, watchlist_id: str) -> Watchlist:
        return m.watchlist(self.t.trading("GET", f"/v2/watchlists/{watchlist_id}"))

    def get_by_name(self, name: str) -> Watchlist:
        return m.watchlist(self.t.trading("GET", "/v2/watchlists:by_name", params={"name": name}))

    def create(self, name: str, symbols: Sequence[str] = ()) -> Watchlist:
        return m.watchlist(self.t.trading("POST", "/v2/watchlists",
                                          json={"name": name, "symbols": [s.upper() for s in symbols]}))

    def update(self, watchlist_id: str, *, name: Optional[str] = None,
               symbols: Optional[Sequence[str]] = None) -> Watchlist:
        cur = self.get(watchlist_id)
        body = {"name": name or cur.name,
                "symbols": [s.upper() for s in symbols] if symbols is not None else cur.symbols}
        return m.watchlist(self.t.trading("PUT", f"/v2/watchlists/{watchlist_id}", json=body))

    def add_symbol(self, watchlist_id: str, symbol: str) -> Watchlist:
        return m.watchlist(self.t.trading("POST", f"/v2/watchlists/{watchlist_id}", json={"symbol": symbol.upper()}))

    def remove_symbol(self, watchlist_id: str, symbol: str) -> Watchlist:
        return m.watchlist(self.t.trading("DELETE", f"/v2/watchlists/{watchlist_id}/{symbol.upper()}"))

    def delete(self, watchlist_id: str) -> None:
        self.t.trading("DELETE", f"/v2/watchlists/{watchlist_id}")
