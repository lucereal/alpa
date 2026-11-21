"""Alpaca JSON -> provider-agnostic models."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Optional

from ...models import (Account, Activity, Bar, CalendarDay, Clock, CorporateAction, Greeks, Mover,
                       NewsItem, OptionContract, Order, PortfolioHistory, Position, Quote, Snapshot,
                       Trade, Watchlist)
from ...timeutil import parse_date, parse_dt

OCC_RE = re.compile(r"^(?P<root>[A-Z0-9.]{1,6})(?P<ymd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


def f(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def i(v) -> Optional[int]:
    x = f(v)
    return None if x is None else int(x)


def is_option_symbol(s: str | None) -> bool:
    return bool(s and OCC_RE.match(s.upper()))


def parse_occ(sym: str) -> Optional[dict]:
    m = OCC_RE.match(sym.upper())
    if not m:
        return None
    ymd = m["ymd"]
    return {
        "underlying": m["root"],
        "expiration": date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6])),
        "type": "call" if m["cp"] == "C" else "put",
        "strike": int(m["strike"]) / 1000.0,
    }


def quote(sym: str, d: dict | None, feed: str | None) -> Optional[Quote]:
    if not d:
        return None
    return Quote(symbol=sym, bid=f(d.get("bp")), ask=f(d.get("ap")), bid_size=f(d.get("bs")),
                 ask_size=f(d.get("as")), timestamp=parse_dt(d.get("t")), feed=feed, raw=d)


def trade(sym: str, d: dict | None, feed: str | None) -> Optional[Trade]:
    if not d:
        return None
    return Trade(symbol=sym, price=f(d.get("p")), size=f(d.get("s")), timestamp=parse_dt(d.get("t")),
                 exchange=d.get("x"), feed=feed, raw=d)


def bar(sym: str, d: dict | None, feed: str | None) -> Optional[Bar]:
    if not d:
        return None
    return Bar(symbol=sym, timestamp=parse_dt(d.get("t")), open=f(d.get("o")), high=f(d.get("h")),
               low=f(d.get("l")), close=f(d.get("c")), volume=f(d.get("v")), vwap=f(d.get("vw")),
               trade_count=i(d.get("n")), feed=feed, raw=d)


def snapshot(sym: str, d: dict, feed: str | None) -> Snapshot:
    return Snapshot(symbol=sym, latest_trade=trade(sym, d.get("latestTrade"), feed),
                    latest_quote=quote(sym, d.get("latestQuote"), feed),
                    minute_bar=bar(sym, d.get("minuteBar"), feed), daily_bar=bar(sym, d.get("dailyBar"), feed),
                    prev_daily_bar=bar(sym, d.get("prevDailyBar"), feed), feed=feed, raw=d)


def mover(d: dict) -> Mover:
    return Mover(symbol=d.get("symbol"), price=f(d.get("price")), change=f(d.get("change")),
                 percent_change=f(d.get("percent_change")), volume=f(d.get("volume")),
                 trade_count=i(d.get("trade_count")))


def news(d: dict) -> NewsItem:
    return NewsItem(id=str(d.get("id")), headline=d.get("headline", ""), created_at=parse_dt(d.get("created_at")),
                    updated_at=parse_dt(d.get("updated_at")), symbols=list(d.get("symbols") or []),
                    source=d.get("source"), author=d.get("author"), summary=d.get("summary") or None,
                    url=d.get("url"), raw=d)


def corporate_action(kind: str, d: dict) -> CorporateAction:
    t = kind[:-1] if kind.endswith("s") else kind
    sym = d.get("symbol") or d.get("old_symbol") or d.get("acquiree_symbol") or ""
    return CorporateAction(type=t, symbol=sym, ex_date=parse_date(d.get("ex_date") or d.get("effective_date")),
                           process_date=parse_date(d.get("process_date")), old_rate=f(d.get("old_rate")),
                           new_rate=f(d.get("new_rate")), rate=f(d.get("rate") or d.get("cash_rate")), raw=d)


def option_contract(d: dict) -> OptionContract:
    return OptionContract(symbol=d.get("symbol"), underlying=d.get("underlying_symbol") or d.get("root_symbol"),
                          expiration=parse_date(d.get("expiration_date")), strike=f(d.get("strike_price")),
                          type=d.get("type"), style=d.get("style"), tradable=d.get("tradable"),
                          open_interest=f(d.get("open_interest")), close_price=f(d.get("close_price")), raw=d)


def option_snapshot(sym: str, d: dict, feed: str | None) -> OptionContract:
    occ = parse_occ(sym) or {}
    g = d.get("greeks")
    return OptionContract(symbol=sym, underlying=occ.get("underlying"), expiration=occ.get("expiration"),
                          strike=occ.get("strike"), type=occ.get("type"),
                          quote=quote(sym, d.get("latestQuote"), feed), last_trade=trade(sym, d.get("latestTrade"), feed),
                          implied_volatility=f(d.get("impliedVolatility")),
                          greeks=Greeks(**{k: f(g.get(k)) for k in ("delta", "gamma", "theta", "vega", "rho")}) if g else None,
                          raw=d)


def account(d: dict, mode: str) -> Account:
    return Account(id=d.get("id"), status=d.get("status"), currency=d.get("currency"), cash=f(d.get("cash")),
                   equity=f(d.get("equity")), buying_power=f(d.get("buying_power")),
                   portfolio_value=f(d.get("portfolio_value")), last_equity=f(d.get("last_equity")),
                   pattern_day_trader=d.get("pattern_day_trader"), daytrade_count=i(d.get("daytrade_count")),
                   options_level=i(d.get("options_trading_level")), trading_blocked=d.get("trading_blocked"),
                   mode=mode, raw=d)


def position(d: dict) -> Position:
    return Position(symbol=d.get("symbol"), qty=f(d.get("qty")), side=d.get("side"),
                    avg_entry_price=f(d.get("avg_entry_price")), market_value=f(d.get("market_value")),
                    cost_basis=f(d.get("cost_basis")), unrealized_pl=f(d.get("unrealized_pl")),
                    unrealized_plpc=f(d.get("unrealized_plpc")), current_price=f(d.get("current_price")),
                    asset_class=d.get("asset_class"), raw=d)


def order(d: dict) -> Order:
    return Order(id=d.get("id"), status=d.get("status"), symbol=d.get("symbol") or None, side=d.get("side") or None,
                 type=d.get("type") or d.get("order_type"), qty=f(d.get("qty")), notional=f(d.get("notional")),
                 filled_qty=f(d.get("filled_qty")), filled_avg_price=f(d.get("filled_avg_price")),
                 limit_price=f(d.get("limit_price")), stop_price=f(d.get("stop_price")),
                 time_in_force=d.get("time_in_force"), order_class=d.get("order_class") or None,
                 client_order_id=d.get("client_order_id"), submitted_at=parse_dt(d.get("submitted_at")),
                 filled_at=parse_dt(d.get("filled_at")), legs=[order(x) for x in (d.get("legs") or [])], raw=d)


def activity(d: dict) -> Activity:
    return Activity(id=d.get("id"), activity_type=d.get("activity_type"),
                    date=d.get("date") or d.get("transaction_time"), symbol=d.get("symbol"), side=d.get("side"),
                    qty=f(d.get("qty")), price=f(d.get("price") or d.get("per_share_amount")),
                    net_amount=f(d.get("net_amount")), raw=d)


def portfolio_history(d: dict) -> PortfolioHistory:
    return PortfolioHistory(
        timestamps=[datetime.fromtimestamp(t, tz=timezone.utc) for t in d.get("timestamp") or []],
        equity=[f(x) for x in d.get("equity") or []], profit_loss=[f(x) for x in d.get("profit_loss") or []],
        profit_loss_pct=[f(x) for x in d.get("profit_loss_pct") or []], base_value=f(d.get("base_value")),
        timeframe=d.get("timeframe"))


def clock(d: dict) -> Clock:
    return Clock(timestamp=parse_dt(d["timestamp"]), is_open=bool(d["is_open"]),
                 next_open=parse_dt(d["next_open"]), next_close=parse_dt(d["next_close"]))


def calendar_day(d: dict) -> CalendarDay:
    return CalendarDay(date=parse_date(d["date"]), open=d.get("open"), close=d.get("close"))


def watchlist(d: dict) -> Watchlist:
    return Watchlist(id=d.get("id"), name=d.get("name"), symbols=[a.get("symbol") for a in d.get("assets") or []],
                     created_at=parse_dt(d.get("created_at")), updated_at=parse_dt(d.get("updated_at")), raw=d)
