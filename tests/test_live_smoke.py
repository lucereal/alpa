"""Read-only smoke test against the real PAPER account. Run:  pytest -m live -s
Never places an order (the order check is dry_run=True and asserts no network submit)."""
import os

import pytest

from alpaca_core import MarketClient

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not os.environ.get("ALPACA_API_KEY_ID"), reason="no Alpaca credentials in env")]


@pytest.fixture(scope="module")
def c():
    with MarketClient() as client:
        assert client.mode == "paper"
        yield client


def test_account(c):
    a = c.account.account()
    assert a.status == "ACTIVE" and a.mode == "paper"
    print(f"\n[account] equity={a.equity:,.2f} buying_power={a.buying_power:,.2f} options_level={a.options_level}")


def test_clock(c):
    k = c.account.clock()
    print(f"\n[clock] is_open={k.is_open} next_open={k.next_open.isoformat()}")
    assert k.next_open and k.next_close


def test_spy_iex_quote(c):
    q = c.market.latest_quote("SPY")
    assert q.feed == "iex" and q.bid and q.ask
    print(f"\n[quote] SPY bid={q.bid} ask={q.ask} feed={q.feed} t={q.timestamp.isoformat()}")


def test_nvda_5_daily_sip_bars(c):
    s = c.market.bars("NVDA", "1Day", days=14, last=5)
    bars = s["NVDA"]
    assert len(bars) == 5 and s.feed == "sip" and not s.fallback
    b = bars[-1]
    print(f"\n[bars] feed={s.feed} clamped={s.end_clamped} last={b.timestamp.date()} c={b.close} v={b.volume:,.0f} vwap={b.vwap}")


def test_nvda_news(c):
    n = c.market.news("NVDA", limit=5)
    assert n and n[0].headline
    print(f"\n[news] {len(n)} items; latest: {n[0].created_at.isoformat()} {n[0].headline[:90]}")


def test_spy_options_chain_sample(c):
    exps = c.options.expirations("SPY", max_dte=10)
    assert exps
    ch = c.options.chain("SPY", exps[min(1, len(exps) - 1)], type="call", limit=25)
    assert ch.contracts and ch.feed == "indicative"
    with_q = [k for k in ch.contracts if k.quote and k.quote.ask]
    k = with_q[0] if with_q else ch.contracts[0]
    print(f"\n[options] {len(exps)} expirations <=10 DTE; sample {k.symbol} bid={k.quote and k.quote.bid} "
          f"ask={k.quote and k.quote.ask} iv={k.implied_volatility}")


def test_dry_run_order_never_sent(c, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("dry run attempted a network call")
    monkeypatch.setattr(c.transport, "request", boom)
    o = c.trading.bracket("NVDA", "buy", 1, limit_price=170, take_profit=180, stop_loss=165, dry_run=True)
    assert o.dry_run and o.status == "dry_run" and o.payload["body"]["order_class"] == "bracket"
    print(f"\n[dry-run] {o.payload['method']} {o.payload['path']} -> {o.payload['body']}")
