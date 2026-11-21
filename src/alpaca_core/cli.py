"""Tiny CLI:  python -m alpaca_core <command> ...   (add --json for raw JSON output)."""
from __future__ import annotations

import argparse
import json
import sys

from .errors import MarketClientError
from .models import to_dict


def _p(obj, as_json: bool):
    if as_json:
        print(json.dumps(to_dict(obj), indent=2, default=str))
    else:
        print(obj)


def _fmt(x, nd=2):
    return "-" if x is None else f"{x:,.{nd}f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m alpaca_core", description="Quick Alpaca checks (paper by default)")
    ap.add_argument("--json", action="store_true", help="print JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("quote", "trade", "snapshot"):
        s = sub.add_parser(name); s.add_argument("symbols", nargs="+"); s.add_argument("--feed", default="auto")
    s = sub.add_parser("bars"); s.add_argument("symbol"); s.add_argument("timeframe", nargs="?", default="1Day")
    s.add_argument("--days", type=int, help="calendar-day lookback"); s.add_argument("--last", type=int, help="keep last N bars")
    s.add_argument("--start"); s.add_argument("--end"); s.add_argument("--feed", default="auto")
    s = sub.add_parser("news"); s.add_argument("symbols", nargs="*"); s.add_argument("--limit", type=int, default=10)
    s = sub.add_parser("movers"); s.add_argument("--top", type=int, default=10)
    s = sub.add_parser("actives"); s.add_argument("--top", type=int, default=10); s.add_argument("--by", default="volume")
    s = sub.add_parser("splits", help="reverse splits (corporate actions)"); s.add_argument("symbols", nargs="*")
    s = sub.add_parser("expirations"); s.add_argument("underlying"); s.add_argument("--max-dte", type=int, default=60)
    s = sub.add_parser("chain"); s.add_argument("underlying"); s.add_argument("--exp"); s.add_argument("--type")
    s.add_argument("--strike-gte", type=float); s.add_argument("--strike-lte", type=float); s.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("crypto"); s.add_argument("symbol")
    for name in ("account", "positions", "clock"):
        sub.add_parser(name)
    s = sub.add_parser("orders"); s.add_argument("--status", default="open"); s.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("watchlists")
    s = sub.add_parser("dry-order", help="validate an order and show the payload (never sent)")
    s.add_argument("symbol"); s.add_argument("side"); s.add_argument("qty", type=float)
    s.add_argument("--limit", type=float); s.add_argument("--stop", type=float); s.add_argument("--take-profit", type=float)
    s = sub.add_parser("serve", help="run the local HTTP service on 127.0.0.1")
    s.add_argument("--port", type=int, default=8765); s.add_argument("--enable-orders", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        from .server import run
        run(port=a.port, enable_orders=a.enable_orders)
        return 0

    from .client import MarketClient
    try:
        with MarketClient() as c:
            return _run(c, a)
    except MarketClientError as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


def _run(c, a) -> int:
    J = a.json
    if a.cmd == "quote":
        r = c.market.latest_quotes(a.symbols, a.feed)
        if J: return _p(r, J) or 0
        for s, q in r.items():
            print(f"{s:6} bid {_fmt(q.bid)} x {q.bid_size}  ask {_fmt(q.ask)} x {q.ask_size}  [{q.feed}] {q.timestamp}")
    elif a.cmd == "trade":
        r = c.market.latest_trades(a.symbols, a.feed)
        if J: return _p(r, J) or 0
        for s, t in r.items():
            print(f"{s:6} {_fmt(t.price)} x {t.size}  [{t.feed}] {t.timestamp}")
    elif a.cmd == "snapshot":
        r = c.market.snapshots(a.symbols, a.feed)
        if J: return _p(r, J) or 0
        for s, x in r.items():
            print(f"{s:6} last {_fmt(x.latest_trade.price if x.latest_trade else None)}  chg {x.change_pct}%  "
                  f"day vol {_fmt(x.daily_bar.volume if x.daily_bar else None, 0)}  [{x.feed}]")
    elif a.cmd == "bars":
        r = c.market.bars(a.symbol, a.timeframe, a.start, a.end, days=a.days, last=a.last or (None if a.days or a.start else 5), feed=a.feed)
        if J: return _p(r, J) or 0
        print(f"feed={r.feed} fallback={r.fallback} clamped={r.end_clamped} {r.note or ''}")
        for b in r[a.symbol]:
            print(f"{b.timestamp:%Y-%m-%d %H:%M}  o {_fmt(b.open)} h {_fmt(b.high)} l {_fmt(b.low)} c {_fmt(b.close)}  v {_fmt(b.volume,0)}  vwap {_fmt(b.vwap)}")
    elif a.cmd == "news":
        r = c.market.news(a.symbols or None, a.limit)
        if J: return _p(r, J) or 0
        for n in r:
            print(f"{n.created_at:%m-%d %H:%M}Z  [{','.join(n.symbols[:4])}] {n.headline}  ({n.source})")
    elif a.cmd == "movers":
        r = c.market.movers(a.top)
        if J: return _p(r, J) or 0
        print("GAINERS:", ", ".join(f"{x.symbol} {x.percent_change:+.1f}%" for x in r.gainers))
        print("LOSERS: ", ", ".join(f"{x.symbol} {x.percent_change:+.1f}%" for x in r.losers))
    elif a.cmd == "actives":
        r = c.market.most_actives(a.top, a.by)
        if J: return _p(r, J) or 0
        print(", ".join(f"{x.symbol} {_fmt(x.volume, 0)}" for x in r))
    elif a.cmd == "splits":
        r = c.market.reverse_splits(a.symbols or None)
        if J: return _p(r, J) or 0
        for x in r:
            print(f"{x.symbol:8} {x.ratio}  ex {x.ex_date}")
        print(f"{len(r)} reverse split(s)")
    elif a.cmd == "expirations":
        _p([d.isoformat() for d in c.options.expirations(a.underlying, max_dte=a.max_dte)], True)
    elif a.cmd == "chain":
        r = c.options.chain(a.underlying, a.exp, type=a.type, strike_gte=a.strike_gte, strike_lte=a.strike_lte, limit=a.limit)
        if J: return _p(r, J) or 0
        for k in r.contracts:
            q = k.quote
            print(f"{k.symbol:22} {k.type:4} {k.strike:>8.2f} {k.expiration}  bid {_fmt(q.bid if q else None)} ask {_fmt(q.ask if q else None)}"
                  f"  iv {_fmt(k.implied_volatility, 3)}  delta {_fmt(k.greeks.delta if k.greeks else None, 3)}")
        print(f"feed={r.feed} contracts={len(r.contracts)}")
    elif a.cmd == "crypto":
        _p(c.crypto.latest_quote(a.symbol), J)
    elif a.cmd == "account":
        x = c.account.account()
        if J: return _p(x, J) or 0
        print(f"[{x.mode}] {x.status}  equity {_fmt(x.equity)}  cash {_fmt(x.cash)}  BP {_fmt(x.buying_power)}  "
              f"day P/L {_fmt(x.day_pl)}  options lvl {x.options_level}")
    elif a.cmd == "positions":
        r = c.account.positions()
        if J: return _p(r, J) or 0
        for p in r:
            print(f"{p.symbol:8} {p.qty:g} @ {_fmt(p.avg_entry_price)}  now {_fmt(p.current_price)}  U P/L {_fmt(p.unrealized_pl)}")
        print(f"{len(r)} position(s)")
    elif a.cmd == "clock":
        _p(c.account.clock(), J)
    elif a.cmd == "orders":
        r = c.trading.orders(a.status, a.limit)
        if J: return _p(r, J) or 0
        for o in r:
            print(f"{o.submitted_at:%m-%d %H:%M} {o.symbol} {o.side} {o.qty} {o.type} {o.status}")
        print(f"{len(r)} order(s)")
    elif a.cmd == "watchlists":
        _p(c.watchlists.list(), J)
    elif a.cmd == "dry-order":
        if a.take_profit and a.stop:
            o = c.trading.bracket(a.symbol, a.side, a.qty, take_profit=a.take_profit, stop_loss=a.stop,
                                  limit_price=a.limit, dry_run=True)
        elif a.stop:
            o = c.trading.stop(a.symbol, a.side, a.qty, a.stop, dry_run=True)
        elif a.limit:
            o = c.trading.limit(a.symbol, a.side, a.qty, a.limit, dry_run=True)
        else:
            o = c.trading.market(a.symbol, a.side, a.qty, dry_run=True)
        print(json.dumps(o.payload, indent=2))
    return 0
