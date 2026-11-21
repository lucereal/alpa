# alpaca_core

`alpaca_core` is a Python client for market data and trading. Your code talks to provider-neutral interfaces (`MarketClient`, the models, and the errors). Alpaca Markets is the current backend, and those details stay in `providers/alpaca/` so another provider can be added later.

It also ships a small CLI and a local JSON HTTP service bound to loopback, so apps in other languages can call the same client.

## Install

Requires Python 3.10 or newer.

```bash
git clone https://github.com/lucereal/alpa.git
cd alpa
python -m venv .venv
source .venv/bin/activate
pip install -e ".[server,dev]"
```

`server` installs FastAPI and uvicorn. `dev` installs pytest and respx. The equivalent extras spec is `pip install -e .[server,dev]` (quote the extras in zsh, where brackets are glob characters).

## Configuration

Credentials come from the environment. Do not hardcode them, and do not commit them.

| Env var | Default | Meaning |
|---|---|---|
| `ALPACA_API_KEY_ID` | required | Alpaca key id. Sent as a header. Never logged. |
| `ALPACA_API_SECRET_KEY` | required | Alpaca secret. Sent as a header. Never logged. |
| `ALPACA_LIVE` | false | See the live-trading lock below. |
| `ALPACA_DATA_PLAN` | `basic` | `basic`: IEX real-time and SIP older than 15 minutes. `sip`: Algo Trader Plus. |
| `ALPACA_OPTIONS_FEED` | `indicative` | `opra` if you are subscribed to OPRA. |
| `ALPACA_RPM` | 190 | Client-side request cap (Alpaca's free plan allows 200/min). |
| `ALPACA_TIMEOUT` | 15 | HTTP timeout in seconds. |
| `ALPACA_MAX_RETRIES` | 4 | Retries for safe calls. Order POSTs are not retried after a 5xx or timeout. |
| `ALPACA_HTTP_ENABLE_ORDERS` | false | Exposes `POST /orders` on the local HTTP service. |

Copy the template and fill in your own paper keys:

```bash
cp .env.example .env
# then export the variables, or load .env in your shell, before running the client
export ALPACA_API_KEY_ID=your_paper_key_id
export ALPACA_API_SECRET_KEY=your_paper_secret_key
```

`.env` is gitignored. `.env.example` contains placeholders only.

### Paper by default, live trading double lock

The client trades on the paper API unless you opt in twice:

1. `ALPACA_LIVE=true` in the environment, and
2. `MarketClient(allow_live=True)` in code.

Either flag alone raises `LiveTradingNotAllowed`. The HTTP service never passes `allow_live`, so it stays on paper even if `ALPACA_LIVE` is set. A real live order from the HTTP service is refused (`dry_run` is forced when the client is not in paper mode).

```python
from alpaca_core import MarketClient

with MarketClient() as c:                 # paper
    print(c.mode)                          # "paper"

# Live only when ALPACA_LIVE=true as well. Otherwise this raises.
with MarketClient(allow_live=True) as c:
    print(c.mode)                          # "live"
```

## CLI

The console script is `alpaca-core`. `python -m alpaca_core` does the same thing. Add `--json` for JSON output.

```bash
python -m alpaca_core account
python -m alpaca_core quote SPY NVDA
python -m alpaca_core bars NVDA 1Day --last 5
python -m alpaca_core news NVDA --limit 10
python -m alpaca_core expirations SPY --max-dte 45
python -m alpaca_core dry-order NVDA buy 1 --limit 170 --take-profit 180 --stop 165
```

`dry-order` validates a payload and prints it. It does not send the order.

`alpaca_test.py` is a separate read-only connection check. It reads the same two env vars, tries paper and then live account endpoints, and prints status plus a few market-data samples. It does not place orders and it does not print the keys.

`run_alpaca_mcp.sh` starts [Alpaca's official MCP server](https://github.com/alpacahq/alpaca-mcp-server) in paper mode via `uvx`. It only remaps `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` onto the names that server expects.

## Python

```python
from alpaca_core import MarketClient, OrderLeg, PlanLimitError

with MarketClient() as c:                          # paper by default
    q = c.market.latest_quote("SPY")               # Quote(bid, ask, mid, spread, feed="iex")
    s = c.market.bars(["NVDA", "AMD"], "5Min", days=3)
    print(s.feed, s.end_clamped, s.fallback, s.note, s["NVDA"][-1].vwap)
    snaps = c.market.snapshots("NVDA,AMD")         # .change_pct, .daily_bar, ...
    c.market.movers(top=10); c.market.most_actives(); c.market.news("NVDA", limit=10)
    c.market.reverse_splits(["NCT"])               # CorporateAction(type, ratio "1-for-8", ex_date)
    exps = c.options.expirations("SPY", max_dte=45)
    chain = c.options.chain("SPY", exps[0], type="call", strike_gte=700, strike_lte=720)
    c.account.account(); c.account.positions(); c.account.portfolio_history("1M", "1D")
    c.account.clock(); c.account.calendar("2026-11-01", "2026-11-30")

    # Orders are validated locally first. dry_run=True returns the payload and sends nothing.
    o = c.trading.bracket("NVDA", "buy", 1, limit_price=170, take_profit=180, stop_loss=165, dry_run=True)
    print(o.payload["body"])
    c.trading.multi_leg([OrderLeg("SPY261219C00700000", "buy", 1, "buy_to_open"),
                         OrderLeg("SPY261219C00710000", "sell", 1, "sell_to_open")], qty=1,
                        limit_price=3.20, dry_run=True)

dry = MarketClient(dry_run=True)                   # every trading call becomes a dry run
```

**Feed policy (`feed="auto"`).**

- Latest quotes, trades, bars, and snapshots use IEX on the basic plan.
- Historical bars use SIP (the full market), with `end` clamped to now minus 15 minutes. If SIP is refused, they retry on IEX and set `fallback=True` and a `note`.
- `BarSeries.feed` and each model's `.feed` record where the data came from.
- Passing `feed="sip"` explicitly raises `PlanLimitError` when the configured plan does not cover it.

**Errors** (all subclass `MarketClientError`): `ConfigError`, `AuthError`, `PlanLimitError` (a subclass of `AuthError`), `RateLimitError`, `NotFoundError` (alias `NotFound`), `ValidationError`, `ServerError`, `ProviderUnavailableError`, `LiveTradingNotAllowed`.

**Transport.**

- A sliding-window limiter keeps traffic at 190 requests per minute or less.
- GET and DELETE retry with backoff on 429, 5xx, and network errors.
- Order POSTs retry only on 429. They are never re-sent after a 5xx or a timeout. Each order gets an `ac-…` client order id.

## HTTP service

The service binds to `127.0.0.1` only and is always paper.

```bash
python -m alpaca_core serve                  # http://127.0.0.1:8765  (docs at /docs)
python -m alpaca_core serve --enable-orders  # adds POST /orders (defaults to dry_run=true)
# scripts/run_server.sh does the same thing with the repo-root .venv
```

| Endpoint | Notes |
|---|---|
| `GET /health` | mode, data plan, whether orders are enabled |
| `GET /quote/{sym}`, `/quotes?symbols=A,B`, `/trades?symbols=` | `feed=auto\|iex\|sip` |
| `GET /snapshots?symbols=A,B` | |
| `GET /bars/{syms}?timeframe=1Day&days=10&last=5&start=&end=&feed=` | returns `{bars, feed, fallback, end_clamped, note}` |
| `GET /news?symbols=NVDA&limit=20`, `/movers?top=10`, `/most-actives` | |
| `GET /corporate-actions?symbols=&types=reverse_split` | |
| `GET /options/expirations/{u}?max_dte=60`, `/options/chain/{u}?expiration=&type=&strike_gte=&strike_lte=&limit=` | |
| `GET /crypto/quote?symbol=BTC/USD` | |
| `GET /account`, `/positions`, `/orders?status=open`, `/clock`, `/calendar`, `/watchlists` | |
| `POST /orders` | only with `--enable-orders`; body is the order fields plus `dry_run` (default true) |

Errors come back as `{"error": "PlanLimitError", "message": "..."}`. Status codes: 401 auth, 402 plan limit, 404, 422 validation, 429, 503 unreachable, 502 other.

From another language, start the service and call it over HTTP:

```bash
curl -s 127.0.0.1:8765/bars/NVDA?last=5 | jq '.bars.NVDA[-1].close'
```

```js
const q = await (await fetch("http://127.0.0.1:8765/quote/SPY")).json();
```

To swap providers, implement the protocols in `alpaca_core/interfaces.py` and register a factory in `client.PROVIDERS`.

## Tests

Unit tests mock HTTP with respx. They do not need API keys and they do not touch the network. `pytest` skips anything marked `live` by default.

```bash
pytest                  # unit tests only
pytest -m live -s       # read-only smoke test against the paper account; needs keys in the environment
```

The live smoke test asserts the client is in paper mode. Its order check uses `dry_run=True` and fails if a network submit is attempted.

## Free (Basic) plan limits

- Real-time stock data comes from IEX only. Volume and spread reflect a thin slice of the market. Use SIP history (older than 15 minutes) for volume and VWAP.
- Queries for recent SIP data return 403, which this client raises as `PlanLimitError`.
- Options data uses the indicative feed. Greeks and implied volatility can be null, including on 0DTE contracts before the open.
- Alpaca allows 200 requests per minute. The client defaults to 190.

## Disclaimer

This software is not financial advice. Nothing here is a recommendation to buy, sell, or hold any security. Trading can lose money. You are responsible for your API keys, the orders you send, and compliance with Alpaca's terms. Use is at your own risk.

## License

No license is chosen yet. Until a license file is added, do not assume you may use, copy, modify, or redistribute this code.
