"""Order placement on Alpaca. Paper by default; optional dry_run returns the validated payload unsent."""
from __future__ import annotations

import logging
import uuid
from dataclasses import replace as dc_replace
from typing import Optional, Sequence

from ...errors import ValidationError
from ...models import Order, OrderLeg, OrderRequest
from ...transport import Transport
from . import mapping as m
from .account import path_symbol

log = logging.getLogger("alpaca_core.trading")

TYPES = {"market", "limit", "stop", "stop_limit", "trailing_stop"}
TIFS = {"day", "gtc", "ioc", "fok", "opg", "cls"}
CLASSES = {None, "simple", "bracket", "oco", "oto", "mleg"}
INTENTS = {None, "buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close"}


def _num(v):
    """Alpaca wants decimal strings; avoid float noise like 0.30000000000000004."""
    if v is None:
        return None
    return format(round(float(v), 6), "f").rstrip("0").rstrip(".") if not float(v).is_integer() else str(int(v))


def validate(req: OrderRequest) -> None:
    """Raise ValidationError for anything we would not want to send. Pure function (unit-testable)."""
    def bad(msg):
        raise ValidationError(f"order rejected locally: {msg}")

    oc = req.order_class
    if oc not in CLASSES:
        bad(f"unknown order_class {oc!r}")
    if req.type not in TYPES:
        bad(f"unknown type {req.type!r}")
    if req.time_in_force not in TIFS:
        bad(f"unknown time_in_force {req.time_in_force!r}")
    if req.position_intent not in INTENTS:
        bad(f"bad position_intent {req.position_intent!r}")
    for name in ("qty", "notional", "limit_price", "stop_price", "trail_price", "trail_percent",
                 "take_profit_limit", "stop_loss_stop", "stop_loss_limit"):
        v = getattr(req, name)
        if v is not None and float(v) <= 0:
            bad(f"{name} must be > 0")

    if oc == "mleg":
        if not 2 <= len(req.legs) <= 4:
            bad("multi-leg orders need 2-4 legs")
        for leg in req.legs:
            if not m.is_option_symbol(leg.symbol):
                bad(f"leg {leg.symbol!r} is not an OCC option symbol")
            if leg.side not in ("buy", "sell"):
                bad("leg side must be buy/sell")
            if leg.position_intent not in INTENTS:
                bad(f"bad leg position_intent {leg.position_intent!r}")
        if not req.qty or not float(req.qty).is_integer():
            bad("multi-leg qty must be a whole number of spreads")
        if req.type not in ("market", "limit"):
            bad("multi-leg type must be market or limit")
        if req.type == "limit" and req.limit_price is None:
            bad("limit multi-leg order needs limit_price")
        if req.time_in_force != "day":
            bad("multi-leg orders must be time_in_force=day")
        return

    if not req.symbol:
        bad("symbol is required")
    if req.side not in ("buy", "sell"):
        bad("side must be 'buy' or 'sell'")
    if (req.qty is None) == (req.notional is None):
        bad("exactly one of qty or notional is required")
    if req.notional is not None and (req.type != "market" or req.time_in_force != "day"):
        bad("notional orders must be market/day")
    if req.type in ("limit", "stop_limit") and req.limit_price is None:
        bad(f"{req.type} order needs limit_price")
    if req.type in ("stop", "stop_limit") and req.stop_price is None:
        bad(f"{req.type} order needs stop_price")
    if req.type == "trailing_stop" and (req.trail_price is None) == (req.trail_percent is None):
        bad("trailing_stop needs exactly one of trail_price / trail_percent")
    if req.extended_hours and not (req.type == "limit" and req.time_in_force == "day"):
        bad("extended_hours requires a limit order with time_in_force=day")

    option = m.is_option_symbol(req.symbol)
    if option:
        if req.qty is None or not float(req.qty).is_integer():
            bad("option qty must be a whole number of contracts")
        if req.time_in_force not in ("day", "gtc"):
            bad("option orders must be day or gtc")
        if req.extended_hours:
            bad("options cannot trade extended hours")
        if oc in ("bracket", "oco", "oto"):
            bad("bracket/oco/oto not supported for options")

    if oc == "bracket":
        if req.take_profit_limit is None or req.stop_loss_stop is None:
            bad("bracket needs take_profit_limit and stop_loss_stop")
        if req.time_in_force not in ("day", "gtc"):
            bad("bracket orders must be day or gtc")
        tp, sl = float(req.take_profit_limit), float(req.stop_loss_stop)
        if req.side == "buy" and not sl < tp:
            bad("buy bracket: stop_loss must be below take_profit")
        if req.side == "sell" and not sl > tp:
            bad("sell bracket: stop_loss must be above take_profit")
        ref = req.limit_price
        if ref is not None:
            if req.side == "buy" and not (sl < float(ref) < tp):
                bad("buy bracket: need stop_loss < limit_price < take_profit")
            if req.side == "sell" and not (tp < float(ref) < sl):
                bad("sell bracket: need take_profit < limit_price < stop_loss")
    if oc in ("oco", "oto") and req.stop_loss_stop is None and req.take_profit_limit is None:
        bad(f"{oc} needs a take_profit_limit and/or stop_loss_stop")


def to_payload(req: OrderRequest) -> dict:
    p: dict = {"type": req.type, "time_in_force": req.time_in_force}
    if req.order_class == "mleg":
        p.update(order_class="mleg", qty=_num(req.qty),
                 legs=[{"symbol": l.symbol.upper(), "side": l.side, "ratio_qty": _num(l.ratio_qty),
                        **({"position_intent": l.position_intent} if l.position_intent else {})} for l in req.legs])
        if req.limit_price is not None:
            p["limit_price"] = _num(req.limit_price)
    else:
        p.update(symbol=req.symbol.upper(), side=req.side)
        if req.qty is not None:
            p["qty"] = _num(req.qty)
        if req.notional is not None:
            p["notional"] = _num(req.notional)
        for k in ("limit_price", "stop_price", "trail_price", "trail_percent"):
            if getattr(req, k) is not None:
                p[k] = _num(getattr(req, k))
        if req.extended_hours:
            p["extended_hours"] = True
        if req.position_intent:
            p["position_intent"] = req.position_intent
        if req.order_class and req.order_class != "simple":
            p["order_class"] = req.order_class
        if req.take_profit_limit is not None:
            p["take_profit"] = {"limit_price": _num(req.take_profit_limit)}
        if req.stop_loss_stop is not None:
            p["stop_loss"] = {"stop_price": _num(req.stop_loss_stop)}
            if req.stop_loss_limit is not None:
                p["stop_loss"]["limit_price"] = _num(req.stop_loss_limit)
    p["client_order_id"] = req.client_order_id or f"ac-{uuid.uuid4().hex[:20]}"
    return p


def _dry(kind: str, payload: dict, method: str, path: str) -> Order:
    return Order(id=f"dry-run-{uuid.uuid4().hex[:8]}", status="dry_run", symbol=payload.get("symbol"),
                 side=payload.get("side"), type=payload.get("type"), qty=m.f(payload.get("qty")),
                 notional=m.f(payload.get("notional")), limit_price=m.f(payload.get("limit_price")),
                 stop_price=m.f(payload.get("stop_price")), time_in_force=payload.get("time_in_force"),
                 order_class=payload.get("order_class"), client_order_id=payload.get("client_order_id"),
                 dry_run=True, payload={"method": method, "path": path, "body": payload, "action": kind})


class AlpacaTrading:
    def __init__(self, transport: Transport, *, dry_run: bool = False):
        self.t = transport
        self.dry_run = dry_run

    @property
    def mode(self) -> str:
        return self.t.config.mode

    def _is_dry(self, dry_run: Optional[bool]) -> bool:
        return self.dry_run if dry_run is None else dry_run

    # ------------------------------------------------------------ submit
    def submit(self, req: OrderRequest, *, dry_run: Optional[bool] = None) -> Order:
        validate(req)
        payload = to_payload(req)
        if self._is_dry(dry_run):
            return _dry("submit", payload, "POST", "/v2/orders")
        log.info("[%s] submitting %s %s %s", self.mode, payload.get("side"), payload.get("qty") or payload.get("notional"),
                 payload.get("symbol") or "mleg")
        o = m.order(self.t.trading("POST", "/v2/orders", json=payload))
        o.payload = payload
        return o

    def _mk(self, symbol, side, dry_run=None, **kw) -> Order:
        return self.submit(OrderRequest(symbol=symbol, side=side, **kw), dry_run=dry_run)

    def market(self, symbol, side, qty=None, notional=None, **kw) -> Order:
        return self._mk(symbol, side, qty=qty, notional=notional, type="market", **kw)

    def limit(self, symbol, side, qty, limit_price, **kw) -> Order:
        return self._mk(symbol, side, qty=qty, limit_price=limit_price, type="limit", **kw)

    def stop(self, symbol, side, qty, stop_price, **kw) -> Order:
        return self._mk(symbol, side, qty=qty, stop_price=stop_price, type="stop", **kw)

    def stop_limit(self, symbol, side, qty, stop_price, limit_price, **kw) -> Order:
        return self._mk(symbol, side, qty=qty, stop_price=stop_price, limit_price=limit_price, type="stop_limit", **kw)

    def trailing_stop(self, symbol, side, qty, *, trail_percent=None, trail_price=None, **kw) -> Order:
        return self._mk(symbol, side, qty=qty, trail_percent=trail_percent, trail_price=trail_price,
                        type="trailing_stop", **kw)

    def bracket(self, symbol, side, qty, *, take_profit, stop_loss, stop_loss_limit=None, limit_price=None, **kw) -> Order:
        kw.setdefault("time_in_force", "day")
        return self._mk(symbol, side, qty=qty, type="limit" if limit_price else "market", limit_price=limit_price,
                        order_class="bracket", take_profit_limit=take_profit, stop_loss_stop=stop_loss,
                        stop_loss_limit=stop_loss_limit, **kw)

    def option(self, contract, side, qty, *, limit_price=None, position_intent=None, **kw) -> Order:
        if not m.is_option_symbol(contract):
            raise ValidationError(f"{contract!r} is not an OCC option symbol")
        return self._mk(contract, side, qty=qty, type="limit" if limit_price else "market", limit_price=limit_price,
                        position_intent=position_intent, **kw)

    def multi_leg(self, legs: Sequence[OrderLeg], qty, *, limit_price=None, dry_run=None, **kw) -> Order:
        req = OrderRequest(symbol=None, side=None, qty=qty, type="limit" if limit_price is not None else "market",
                           limit_price=limit_price, order_class="mleg", legs=list(legs), **kw)
        return self.submit(req, dry_run=dry_run)

    # ------------------------------------------------------------ modify / cancel
    def replace(self, order_id: str, *, qty=None, limit_price=None, stop_price=None, time_in_force=None,
                trail=None, dry_run: Optional[bool] = None) -> Order:
        body = {k: _num(v) if k != "time_in_force" else v for k, v in
                {"qty": qty, "limit_price": limit_price, "stop_price": stop_price, "trail": trail,
                 "time_in_force": time_in_force}.items() if v is not None}
        if not body:
            raise ValidationError("replace needs at least one field to change")
        if self._is_dry(dry_run):
            return _dry("replace", body, "PATCH", f"/v2/orders/{order_id}")
        return m.order(self.t.trading("PATCH", f"/v2/orders/{order_id}", json=body))

    def cancel(self, order_id: str, *, dry_run: Optional[bool] = None) -> None:
        if self._is_dry(dry_run):
            return None
        self.t.trading("DELETE", f"/v2/orders/{order_id}")

    def cancel_all(self, *, dry_run: Optional[bool] = None) -> list[dict]:
        if self._is_dry(dry_run):
            return [{"dry_run": True, "action": "cancel_all"}]
        return self.t.trading("DELETE", "/v2/orders") or []

    def get_order(self, order_id: str) -> Order:
        return m.order(self.t.trading("GET", f"/v2/orders/{order_id}", params={"nested": "true"}))

    def get_order_by_client_id(self, client_order_id: str) -> Order:
        return m.order(self.t.trading("GET", "/v2/orders:by_client_order_id",
                                      params={"client_order_id": client_order_id}))

    def orders(self, status: str = "open", limit: int = 50, symbols=None) -> list[Order]:
        if status not in ("open", "closed", "all"):
            raise ValidationError("status must be open|closed|all")
        syms = ",".join(symbols) if isinstance(symbols, (list, tuple)) else symbols
        b = self.t.trading("GET", "/v2/orders", params={"status": status, "limit": limit, "nested": "true",
                                                          "symbols": syms, "direction": "desc"})
        return [m.order(x) for x in b or []]

    # ------------------------------------------------------------ positions
    def close_position(self, symbol: str, *, qty=None, percentage=None, dry_run: Optional[bool] = None) -> Order:
        if qty is not None and percentage is not None:
            raise ValidationError("pass qty or percentage, not both")
        params = {"qty": _num(qty), "percentage": _num(percentage)}
        path = f"/v2/positions/{path_symbol(symbol)}"
        if self._is_dry(dry_run):
            return _dry("close_position", {"symbol": symbol.upper(), **{k: v for k, v in params.items() if v}},
                        "DELETE", path)
        return m.order(self.t.trading("DELETE", path, params=params, retry_on_network=False))

    def close_all_positions(self, *, cancel_orders: bool = True, dry_run: Optional[bool] = None) -> list[dict]:
        if self._is_dry(dry_run):
            return [{"dry_run": True, "action": "close_all_positions", "cancel_orders": cancel_orders}]
        return self.t.trading("DELETE", "/v2/positions", params={"cancel_orders": str(cancel_orders).lower()},
                              retry_on_network=False) or []

    def exercise(self, contract: str, *, dry_run: Optional[bool] = None) -> None:
        if self._is_dry(dry_run):
            return None
        self.t.trading("POST", f"/v2/positions/{contract.upper()}/exercise")
