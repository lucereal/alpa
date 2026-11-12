"""Unit tests - all HTTP mocked with respx; no network."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from alpaca_core import (AuthError, Config, LiveTradingNotAllowed, MarketClient, NotFoundError, OrderLeg,
                         PlanLimitError, RateLimitError, ServerError, ValidationError)
from alpaca_core.providers.alpaca.mapping import parse_occ
from alpaca_core.transport import RateLimiter

from .conftest import DATA, TRADING

SIP_403 = {"message": "subscription does not permit querying recent SIP data"}


# ---------------------------------------------------------------- config / safety
def test_config_paper_default(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k"); monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    monkeypatch.delenv("ALPACA_LIVE", raising=False)
    c = Config.from_env()
    assert c.mode == "paper" and c.trading_url == TRADING

    assert "api_key_id" not in repr(c) and "api_secret_key" not in repr(c)


@pytest.mark.parametrize("env_live,allow,ok", [("true", True, True), ("true", False, False), ("false", True, False)])
def test_live_requires_both(monkeypatch, env_live, allow, ok):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "k"); monkeypatch.setenv("ALPACA_API_SECRET_KEY", "s")
    monkeypatch.setenv("ALPACA_LIVE", env_live)
    if ok:
        c = Config.from_env(allow_live=allow)
        assert c.live and c.trading_url == "https://api.alpaca.markets"
    else:
        with pytest.raises(LiveTradingNotAllowed):
            Config.from_env(allow_live=allow)


def test_live_config_without_flag_rejected(cfg):
    from dataclasses import replace
    with pytest.raises(LiveTradingNotAllowed):
        MarketClient(replace(cfg, live=True))


# ---------------------------------------------------------------- transport
@respx.mock
def test_auth_headers_and_retry_on_429(client):
    route = respx.get(f"{TRADING}/v2/clock").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"timestamp": "2026-11-12T08:00:00Z", "is_open": False,
                                  "next_open": "2026-11-12T09:30:00-05:00", "next_close": "2026-11-12T16:00:00-05:00"})])
    clk = client.account.clock()
    assert route.call_count == 2 and clk.is_open is False
    req = route.calls[0].request
    assert req.headers["APCA-API-KEY-ID"] == "TESTKEY" and req.headers["APCA-API-SECRET-KEY"] == "TESTSECRET"


@respx.mock
def test_retry_5xx_then_give_up(client):
    route = respx.get(f"{TRADING}/v2/account").mock(return_value=httpx.Response(503, text="down"))
    with pytest.raises(ServerError):
        client.account.account()
    assert route.call_count == 4  # 1 + max_retries(3)


@respx.mock
def test_order_post_not_retried_on_5xx(client):
    route = respx.post(f"{TRADING}/v2/orders").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(ServerError):
        client.trading.market("SPY", "buy", 1)
    assert route.call_count == 1


@respx.mock
def test_rate_limit_exhausted(client):
    respx.get(f"{TRADING}/v2/account").mock(return_value=httpx.Response(429))
    with pytest.raises(RateLimitError):
        client.account.account()


@respx.mock
@pytest.mark.parametrize("status,body,exc", [
    (401, {"message": "unauthorized"}, AuthError), (403, {"message": "forbidden"}, AuthError),
    (403, SIP_403, PlanLimitError), (404, {"message": "not found"}, NotFoundError),
    (422, {"message": "bad qty"}, ValidationError)])
def test_error_mapping(client, status, body, exc):
    respx.get(f"{TRADING}/v2/account").mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(exc):
        client.account.account()


def test_rate_limiter_blocks_when_full():
    now = [0.0]; slept = []
    def sleep(s):
        slept.append(s); now[0] += s
    rl = RateLimiter(3, window=60, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        rl.acquire()
    assert not slept
    rl.acquire()
    assert slept and abs(sum(slept) - 60) < 0.1


# ---------------------------------------------------------------- market data / feeds
Q = {"t": "2026-11-11T21:00:00Z", "bp": 759.99, "bs": 3, "ap": 760.13, "as": 2}


@respx.mock
def test_latest_quote_auto_uses_iex(client):
    route = respx.get(f"{DATA}/v2/stocks/quotes/latest").mock(return_value=httpx.Response(200, json={"quotes": {"SPY": Q}}))
    q = client.market.latest_quote("spy")
    assert route.calls[0].request.url.params["feed"] == "iex"
    assert q.feed == "iex" and q.bid == 759.99 and q.mid == 760.06


@respx.mock
def test_latest_quote_explicit_sip_raises_plan_limit(client):
    respx.get(f"{DATA}/v2/stocks/quotes/latest").mock(return_value=httpx.Response(403, json=SIP_403))
    with pytest.raises(PlanLimitError):
        client.market.latest_quote("SPY", feed="sip")


@respx.mock
def test_latest_quote_missing_symbol(client):
    respx.get(f"{DATA}/v2/stocks/quotes/latest").mock(return_value=httpx.Response(200, json={"quotes": {}}))
    with pytest.raises(NotFoundError):
        client.market.latest_quote("ZZZZ")


def _bar(day):
    return {"t": f"2026-11-{day:02d}T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100, "vw": 1.2, "n": 9}


@respx.mock
def test_bars_auto_sip_clamped_and_paginated(client):
    route = respx.get(f"{DATA}/v2/stocks/bars").mock(side_effect=[
        httpx.Response(200, json={"bars": {"NVDA": [_bar(9), _bar(10)]}, "next_page_token": "abc"}),
        httpx.Response(200, json={"bars": {"NVDA": [_bar(11)]}, "next_page_token": None})])
    s = client.market.bars("NVDA", "1Day", days=7)
    p = route.calls[0].request.url.params
    assert p["feed"] == "sip" and route.calls[1].request.url.params["page_token"] == "abc"
    end = datetime.strptime(p["end"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert end <= datetime.now(timezone.utc) - timedelta(minutes=14)
    assert s.feed == "sip" and s.end_clamped and not s.fallback and len(s["NVDA"]) == 3
    assert s["NVDA"][0].vwap == 1.2


@respx.mock
def test_bars_sip_refused_falls_back_to_iex(client):
    route = respx.get(f"{DATA}/v2/stocks/bars").mock(side_effect=[
        httpx.Response(403, json=SIP_403), httpx.Response(200, json={"bars": {"NVDA": [_bar(11)]}})])
    s = client.market.bars("NVDA", "1Day", days=3, last=1)
    assert [c.request.url.params["feed"] for c in route.calls] == ["sip", "iex"]
    assert s.feed == "iex" and s.fallback and "fell back" in s.note and s["NVDA"][0].feed == "iex"


@respx.mock
def test_bars_old_window_not_clamped(client):
    respx.get(f"{DATA}/v2/stocks/bars").mock(return_value=httpx.Response(200, json={"bars": {}}))
    s = client.market.bars("SPY", "1Hour", start="2026-01-02", end="2026-01-03")
    assert s.feed == "sip" and not s.end_clamped


def test_bad_timeframe(client):
    with pytest.raises(ValidationError):
        client.market.bars("SPY", "7Fortnights")


@respx.mock
def test_snapshots_movers_news_splits(client):
    respx.get(f"{DATA}/v2/stocks/snapshots").mock(return_value=httpx.Response(200, json={"NVDA": {
        "latestTrade": {"t": "2026-11-11T21:00:00Z", "p": 110.0, "s": 5}, "latestQuote": Q,
        "dailyBar": _bar(11), "prevDailyBar": {**_bar(10), "c": 100.0}}}))
    respx.get(f"{DATA}/v1beta1/screener/stocks/movers").mock(return_value=httpx.Response(200, json={
        "gainers": [{"symbol": "AAA", "percent_change": 50, "price": 3}], "losers": [], "last_updated": "x"}))
    respx.get(f"{DATA}/v1beta1/news").mock(return_value=httpx.Response(200, json={"news": [
        {"id": 1, "headline": "H", "created_at": "2026-11-12T07:41:00Z", "symbols": ["NVDA"], "source": "benzinga"}]}))
    respx.get(f"{DATA}/v1/corporate-actions").mock(return_value=httpx.Response(200, json={"corporate_actions": {
        "reverse_splits": [{"symbol": "NCT", "new_rate": 1, "old_rate": 8, "ex_date": "2026-11-16"}]}}))
    snap = client.market.snapshot("NVDA")
    assert snap.change_pct == 10.0 and snap.feed == "iex"
    assert client.market.movers().gainers[0].symbol == "AAA"
    assert client.market.news("NVDA")[0].headline == "H"
    rs = client.market.reverse_splits(["NCT"])
    assert rs[0].type == "reverse_split" and rs[0].ratio == "1-for-8"


# ---------------------------------------------------------------- options
def test_parse_occ():
    o = parse_occ("SPY261219C00600000")
    assert o["underlying"] == "SPY" and o["strike"] == 600.0 and o["type"] == "call" and o["expiration"].day == 19


@respx.mock
def test_chain_and_expirations(client):
    respx.get(f"{DATA}/v1beta1/options/snapshots/SPY").mock(return_value=httpx.Response(200, json={"snapshots": {
        "SPY261219P00600000": {"latestQuote": {"bp": 1.0, "ap": 1.1}, "greeks": {"delta": -0.2}, "impliedVolatility": 0.2},
        "SPY261219C00600000": {"latestQuote": {"bp": 9.0, "ap": 9.2}}}}))
    respx.get(f"{TRADING}/v2/options/contracts").mock(return_value=httpx.Response(200, json={"option_contracts": [
        {"symbol": "SPY261219C00600000", "underlying_symbol": "SPY", "expiration_date": "2026-12-19",
         "strike_price": "600", "type": "call"},
        {"symbol": "SPY261120C00600000", "underlying_symbol": "SPY", "expiration_date": "2026-11-20",
         "strike_price": "600", "type": "call"}]}))
    ch = client.options.chain("spy")
    assert ch.feed == "indicative" and len(ch.calls()) == 1 and ch.puts()[0].greeks.delta == -0.2
    assert [d.isoformat() for d in client.options.expirations("SPY")] == ["2026-11-20", "2026-12-19"]


# ---------------------------------------------------------------- trading
@respx.mock
def test_dry_run_sends_nothing(client):
    route = respx.post(f"{TRADING}/v2/orders")
    o = client.trading.bracket("NVDA", "buy", 1, limit_price=170, take_profit=180, stop_loss=165, dry_run=True)
    assert not route.called and o.dry_run and o.status == "dry_run"
    b = o.payload["body"]
    assert b["order_class"] == "bracket" and b["stop_loss"] == {"stop_price": "165"} and b["qty"] == "1"


def test_dry_run_client_wide(cfg):
    c = MarketClient(cfg, dry_run=True)
    assert c.trading.stop("SPY", "sell", 2, 750.5).payload["body"]["stop_price"] == "750.5"
    assert c.trading.cancel_all() == [{"dry_run": True, "action": "cancel_all"}]
    assert c.trading.close_position("SPY").payload["method"] == "DELETE"


@respx.mock
def test_submit_market_order(client):
    route = respx.post(f"{TRADING}/v2/orders").mock(return_value=httpx.Response(200, json={
        "id": "o1", "status": "accepted", "symbol": "SPY", "side": "buy", "type": "market", "qty": "1"}))
    o = client.trading.market("SPY", "buy", 1)
    assert o.id == "o1" and not o.dry_run
    import json
    assert json.loads(route.calls[0].request.content)["client_order_id"].startswith("ac-")


@pytest.mark.parametrize("kw", [
    dict(symbol="SPY", side="buy"),                                       # no qty
    dict(symbol="SPY", side="buy", qty=1, notional=100),                  # both
    dict(symbol="SPY", side="hold", qty=1),                               # bad side
    dict(symbol="SPY", side="buy", qty=1, type="limit"),                  # limit w/o price
    dict(symbol="SPY", side="buy", qty=1, type="stop"),                   # stop w/o price
    dict(symbol="SPY", side="buy", qty=1, type="market", extended_hours=True),
    dict(symbol="SPY", side="buy", qty=-1),
    dict(symbol="SPY", side="buy", qty=1, order_class="bracket", take_profit_limit=100, stop_loss_stop=110),
    dict(symbol="SPY261219C00600000", side="buy", qty=1.5),               # fractional option
])
def test_validation_rejects(client, kw):
    from alpaca_core.models import OrderRequest
    with pytest.raises(ValidationError):
        client.trading.submit(OrderRequest(**kw), dry_run=True)


def test_multi_leg_payload(client):
    legs = [OrderLeg("SPY261219C00600000", "buy", 1, "buy_to_open"),
            OrderLeg("SPY261219C00610000", "sell", 1, "sell_to_open")]
    o = client.trading.multi_leg(legs, 2, limit_price=3.25, dry_run=True)
    b = o.payload["body"]
    assert b["order_class"] == "mleg" and b["qty"] == "2" and b["limit_price"] == "3.25" and len(b["legs"]) == 2
    with pytest.raises(ValidationError):
        client.trading.multi_leg(legs[:1], 1, dry_run=True)


@respx.mock
def test_watchlist_crud(client):
    wl = {"id": "w1", "name": "quant", "assets": [{"symbol": "NVDA"}]}
    respx.post(f"{TRADING}/v2/watchlists").mock(return_value=httpx.Response(200, json=wl))
    respx.post(f"{TRADING}/v2/watchlists/w1").mock(return_value=httpx.Response(200, json={**wl, "assets": [{"symbol": "NVDA"}, {"symbol": "AMD"}]}))
    respx.delete(f"{TRADING}/v2/watchlists/w1").mock(return_value=httpx.Response(204))
    assert client.watchlists.create("quant", ["nvda"]).symbols == ["NVDA"]
    assert client.watchlists.add_symbol("w1", "amd").symbols == ["NVDA", "AMD"]
    assert client.watchlists.delete("w1") is None


# ---------------------------------------------------------------- HTTP service
def _app(client, **kw):
    from fastapi.testclient import TestClient
    from alpaca_core.server import create_app
    return TestClient(create_app(client, **kw))


@respx.mock
def test_server_read_endpoints_and_errors(client):
    respx.get(f"{DATA}/v2/stocks/quotes/latest").mock(return_value=httpx.Response(200, json={"quotes": {"SPY": Q}}))
    respx.get(f"{TRADING}/v2/account").mock(return_value=httpx.Response(401, json={"message": "no"}))
    with _app(client) as http:
        assert http.get("/health").json()["orders_enabled"] is False
        r = http.get("/quote/SPY")
        assert r.status_code == 200 and r.json()["bid"] == 759.99 and r.json()["feed"] == "iex"
        assert http.get("/account").status_code == 401
        assert http.post("/orders", json={"symbol": "SPY", "side": "buy", "qty": 1}).status_code == 405


@respx.mock
def test_server_orders_flag_defaults_to_dry_run(client):
    route = respx.post(f"{TRADING}/v2/orders")
    with _app(client, enable_orders=True) as http:
        r = http.post("/orders", json={"symbol": "SPY", "side": "buy", "qty": 1, "type": "limit", "limit_price": 700})
        assert r.status_code == 200 and r.json()["dry_run"] is True and not route.called
        assert http.post("/orders", json={"symbol": "SPY", "side": "buy"}).status_code == 422


def test_server_refuses_non_loopback():
    from alpaca_core.server import run
    with pytest.raises(SystemExit):
        run(host="0.0.0.0")


def test_parse_dt_keeps_offset_with_nanos():
    from alpaca_core.timeutil import parse_dt
    d = parse_dt("2026-11-12T02:57:22.262130123-05:00")
    assert d.utcoffset() == timedelta(hours=-5) and d.microsecond == 262130
    assert parse_dt("2026-11-11T21:00:13.156091123Z").tzinfo is not None
