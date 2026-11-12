"""Local JSON HTTP service so non-Python apps can use alpaca_core.

Binds to 127.0.0.1 only. Always paper (the server never passes allow_live). Order placement is OFF
unless started with --enable-orders / ALPACA_HTTP_ENABLE_ORDERS=true, and even then requests default
to dry_run=true; a real (paper) submit requires {"dry_run": false} in the body.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import List, Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .client import MarketClient
from .errors import (AuthError, MarketClientError, NotFoundError, PlanLimitError, ProviderUnavailableError,
                     RateLimitError, ValidationError)
from .models import OrderLeg, OrderRequest, to_dict

LOOPBACK = {"127.0.0.1", "localhost", "::1"}

_STATUS = [(PlanLimitError, 402), (AuthError, 401), (NotFoundError, 404), (ValidationError, 422),
           (RateLimitError, 429), (ProviderUnavailableError, 503)]


class LegIn(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    ratio_qty: float = 1
    position_intent: Optional[str] = None


class OrderIn(BaseModel):
    symbol: Optional[str] = None
    side: Optional[Literal["buy", "sell"]] = None
    qty: Optional[float] = None
    notional: Optional[float] = None
    type: str = "market"
    time_in_force: str = "day"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_price: Optional[float] = None
    trail_percent: Optional[float] = None
    order_class: Optional[str] = None
    take_profit_limit: Optional[float] = None
    stop_loss_stop: Optional[float] = None
    stop_loss_limit: Optional[float] = None
    position_intent: Optional[str] = None
    client_order_id: Optional[str] = None
    legs: List[LegIn] = Field(default_factory=list)
    dry_run: bool = True


def create_app(client: Optional[MarketClient] = None, *, enable_orders: Optional[bool] = None) -> FastAPI:
    if enable_orders is None:
        enable_orders = os.environ.get("ALPACA_HTTP_ENABLE_ORDERS", "").lower() in ("1", "true", "yes")
    state = {"client": client}

    @asynccontextmanager
    async def lifespan(app):
        if state["client"] is None:
            state["client"] = MarketClient()  # never allow_live -> always paper
        yield
        state["client"].close()

    app = FastAPI(title="alpaca_core local service", version="0.1.0", lifespan=lifespan)

    def c() -> MarketClient:
        return state["client"]

    @app.exception_handler(MarketClientError)
    async def _err(_: Request, e: MarketClientError):
        code = next((s for cls, s in _STATUS if isinstance(e, cls)), 502)
        return JSONResponse(status_code=code, content={"error": type(e).__name__, "message": str(e)})

    J = to_dict

    @app.get("/health")
    def health():
        return {"ok": True, "mode": c().mode, "data_plan": c().config.data_plan, "orders_enabled": enable_orders}

    @app.get("/quotes")
    def quotes(symbols: str, feed: str = "auto"):
        return J(c().market.latest_quotes(symbols, feed))

    @app.get("/quote/{symbol}")
    def quote(symbol: str, feed: str = "auto"):
        return J(c().market.latest_quote(symbol, feed))

    @app.get("/trades")
    def trades(symbols: str, feed: str = "auto"):
        return J(c().market.latest_trades(symbols, feed))

    @app.get("/snapshots")
    def snapshots(symbols: str, feed: str = "auto"):
        return J(c().market.snapshots(symbols, feed))

    @app.get("/bars/{symbols}")
    def bars(symbols: str, timeframe: str = "1Day", start: Optional[str] = None, end: Optional[str] = None,
             days: Optional[int] = None, last: Optional[int] = None, limit: Optional[int] = None, feed: str = "auto"):
        return J(c().market.bars(symbols, timeframe, start, end, days=days, last=last, limit=limit, feed=feed))

    @app.get("/news")
    def news(symbols: Optional[str] = None, limit: int = Query(20, le=200)):
        return J(c().market.news(symbols, limit))

    @app.get("/movers")
    def movers(top: int = Query(10, le=50)):
        return J(c().market.movers(top))

    @app.get("/most-actives")
    def most_actives(top: int = Query(10, le=100), by: str = "volume"):
        return J(c().market.most_actives(top, by))

    @app.get("/corporate-actions")
    def corporate_actions(symbols: Optional[str] = None, types: Optional[str] = None,
                          start: Optional[str] = None, end: Optional[str] = None):
        return J(c().market.corporate_actions(symbols, types.split(",") if types else None, start, end))

    @app.get("/options/expirations/{underlying}")
    def expirations(underlying: str, min_dte: int = 0, max_dte: Optional[int] = 60):
        return [d.isoformat() for d in c().options.expirations(underlying, min_dte=min_dte, max_dte=max_dte)]

    @app.get("/options/chain/{underlying}")
    def chain(underlying: str, expiration: Optional[str] = None, type: Optional[str] = None,
              strike_gte: Optional[float] = None, strike_lte: Optional[float] = None, limit: int = Query(200, le=5000)):
        return J(c().options.chain(underlying, expiration, type=type, strike_gte=strike_gte,
                                   strike_lte=strike_lte, limit=limit))

    @app.get("/crypto/quote")
    def crypto_quote(symbol: str):
        return J(c().crypto.latest_quote(symbol))

    @app.get("/account")
    def account():
        return J(c().account.account())

    @app.get("/positions")
    def positions():
        return J(c().account.positions())

    @app.get("/orders")
    def orders(status: str = "open", limit: int = Query(50, le=500)):
        return J(c().trading.orders(status, limit))

    @app.get("/clock")
    def clock():
        return J(c().account.clock())

    @app.get("/calendar")
    def calendar(start: Optional[str] = None, end: Optional[str] = None):
        return J(c().account.calendar(start, end))

    @app.get("/watchlists")
    def watchlists():
        return J(c().watchlists.list())

    if enable_orders:
        @app.post("/orders")
        def place_order(body: OrderIn):
            cl = c()
            dry = body.dry_run or cl.mode != "paper"   # HTTP can never send a live order
            data = body.model_dump(exclude={"dry_run", "legs"})
            req = OrderRequest(**data, legs=[OrderLeg(**l.model_dump()) for l in body.legs])
            return J(cl.trading.submit(req, dry_run=dry))

    return app


def run(host: str = "127.0.0.1", port: int = 8765, enable_orders: bool = False) -> None:
    if host not in LOOPBACK:
        raise SystemExit(f"refusing to bind to {host}; this service is loopback-only")
    import uvicorn
    uvicorn.run(create_app(enable_orders=enable_orders), host=host, port=port, log_level="info")
