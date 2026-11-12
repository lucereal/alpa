"""Provider-agnostic typed models. Every model keeps `raw` (the provider payload) for passthrough."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import date, datetime
from typing import Any, Optional


def to_dict(obj: Any) -> Any:
    """JSON-friendly conversion (datetimes -> ISO strings). Drops `raw` unless keep_raw."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj) if f.name != "raw"}
    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


class _Model:
    def to_dict(self) -> dict:
        return to_dict(self)


@dataclass
class Quote(_Model):
    symbol: str
    bid: Optional[float]
    ask: Optional[float]
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None
    timestamp: Optional[datetime] = None
    feed: Optional[str] = None          # e.g. "iex", "sip", "indicative", "crypto"
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def mid(self) -> Optional[float]:
        if self.bid and self.ask:
            return round((self.bid + self.ask) / 2, 6)
        return None

    @property
    def spread(self) -> Optional[float]:
        if self.bid and self.ask:
            return round(self.ask - self.bid, 6)
        return None


@dataclass
class Trade(_Model):
    symbol: str
    price: float
    size: Optional[float] = None
    timestamp: Optional[datetime] = None
    exchange: Optional[str] = None
    feed: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Bar(_Model):
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: Optional[float] = None
    trade_count: Optional[int] = None
    feed: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class BarSeries(_Model):
    """Historical bars for one or more symbols plus provenance of the data."""
    bars: dict[str, list[Bar]]
    timeframe: str
    feed: str                       # feed actually used
    requested_feed: str             # what the caller asked for ("auto", "sip", "iex"...)
    fallback: bool = False          # True if we had to fall back (e.g. SIP refused -> IEX)
    end_clamped: bool = False       # True if `end` was pulled back to respect the SIP delay
    note: Optional[str] = None

    def __getitem__(self, symbol: str) -> list[Bar]:
        return self.bars.get(symbol.upper(), [])


@dataclass
class Snapshot(_Model):
    symbol: str
    latest_trade: Optional[Trade] = None
    latest_quote: Optional[Quote] = None
    minute_bar: Optional[Bar] = None
    daily_bar: Optional[Bar] = None
    prev_daily_bar: Optional[Bar] = None
    feed: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def change_pct(self) -> Optional[float]:
        if self.prev_daily_bar and self.latest_trade and self.prev_daily_bar.close:
            return round((self.latest_trade.price / self.prev_daily_bar.close - 1) * 100, 3)
        return None


@dataclass
class Mover(_Model):
    symbol: str
    price: Optional[float] = None
    change: Optional[float] = None
    percent_change: Optional[float] = None
    volume: Optional[float] = None
    trade_count: Optional[int] = None


@dataclass
class Movers(_Model):
    gainers: list[Mover]
    losers: list[Mover]
    last_updated: Optional[str] = None


@dataclass
class NewsItem(_Model):
    id: str
    headline: str
    created_at: Optional[datetime]
    symbols: list[str] = field(default_factory=list)
    source: Optional[str] = None
    author: Optional[str] = None
    summary: Optional[str] = None
    url: Optional[str] = None
    updated_at: Optional[datetime] = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class CorporateAction(_Model):
    type: str                         # "reverse_split", "forward_split", "cash_dividend", ...
    symbol: str
    ex_date: Optional[date] = None
    process_date: Optional[date] = None
    old_rate: Optional[float] = None
    new_rate: Optional[float] = None
    rate: Optional[float] = None      # dividends
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def ratio(self) -> Optional[str]:
        if self.old_rate and self.new_rate:
            return f"{self.new_rate:g}-for-{self.old_rate:g}"
        return None


@dataclass
class Greeks(_Model):
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    rho: Optional[float] = None


@dataclass
class OptionContract(_Model):
    symbol: str                       # OCC symbol e.g. SPY251219C00600000
    underlying: str
    expiration: date
    strike: float
    type: str                         # "call" | "put"
    style: Optional[str] = None
    tradable: Optional[bool] = None
    open_interest: Optional[float] = None
    close_price: Optional[float] = None
    # snapshot fields (filled for chain results)
    quote: Optional[Quote] = None
    last_trade: Optional[Trade] = None
    implied_volatility: Optional[float] = None
    greeks: Optional[Greeks] = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class OptionChain(_Model):
    underlying: str
    contracts: list[OptionContract]
    feed: Optional[str] = None

    @property
    def expirations(self) -> list[date]:
        return sorted({c.expiration for c in self.contracts})

    def calls(self) -> list[OptionContract]:
        return [c for c in self.contracts if c.type == "call"]

    def puts(self) -> list[OptionContract]:
        return [c for c in self.contracts if c.type == "put"]


@dataclass
class Account(_Model):
    id: str
    status: str
    currency: str
    cash: float
    equity: float
    buying_power: float
    portfolio_value: Optional[float] = None
    last_equity: Optional[float] = None
    pattern_day_trader: Optional[bool] = None
    daytrade_count: Optional[int] = None
    options_level: Optional[int] = None
    trading_blocked: Optional[bool] = None
    mode: Optional[str] = None        # "paper" | "live"
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def day_pl(self) -> Optional[float]:
        return None if self.last_equity is None else round(self.equity - self.last_equity, 2)


@dataclass
class Position(_Model):
    symbol: str
    qty: float
    side: str
    avg_entry_price: float
    market_value: Optional[float] = None
    cost_basis: Optional[float] = None
    unrealized_pl: Optional[float] = None
    unrealized_plpc: Optional[float] = None
    current_price: Optional[float] = None
    asset_class: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class OrderLeg(_Model):
    symbol: str
    side: str                          # "buy" | "sell"
    ratio_qty: float = 1
    position_intent: Optional[str] = None   # buy_to_open, sell_to_close, ...


@dataclass
class OrderRequest(_Model):
    """Provider-agnostic order request. Build via Trading helpers or directly."""
    symbol: Optional[str]
    side: Optional[str]
    qty: Optional[float] = None
    notional: Optional[float] = None
    type: str = "market"               # market | limit | stop | stop_limit | trailing_stop
    time_in_force: str = "day"         # day | gtc | ioc | fok | opg | cls
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_price: Optional[float] = None
    trail_percent: Optional[float] = None
    extended_hours: bool = False
    client_order_id: Optional[str] = None
    order_class: Optional[str] = None  # simple | bracket | oco | oto | mleg
    take_profit_limit: Optional[float] = None
    stop_loss_stop: Optional[float] = None
    stop_loss_limit: Optional[float] = None
    position_intent: Optional[str] = None
    legs: list[OrderLeg] = field(default_factory=list)


@dataclass
class Order(_Model):
    id: str
    status: str
    symbol: Optional[str]
    side: Optional[str]
    type: Optional[str]
    qty: Optional[float] = None
    notional: Optional[float] = None
    filled_qty: Optional[float] = None
    filled_avg_price: Optional[float] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: Optional[str] = None
    order_class: Optional[str] = None
    client_order_id: Optional[str] = None
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    legs: list["Order"] = field(default_factory=list)
    dry_run: bool = False
    payload: Optional[dict] = None     # request body (always set for dry runs)
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Activity(_Model):
    id: str
    activity_type: str
    date: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    qty: Optional[float] = None
    price: Optional[float] = None
    net_amount: Optional[float] = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class PortfolioHistory(_Model):
    timestamps: list[datetime]
    equity: list[Optional[float]]
    profit_loss: list[Optional[float]]
    profit_loss_pct: list[Optional[float]]
    base_value: Optional[float] = None
    timeframe: Optional[str] = None


@dataclass
class Clock(_Model):
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


@dataclass
class CalendarDay(_Model):
    date: date
    open: str
    close: str


@dataclass
class Watchlist(_Model):
    id: str
    name: str
    symbols: list[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    raw: dict = field(default_factory=dict, repr=False)


__all__ = [n for n in dir() if n[0].isupper()] + ["to_dict"]
