"""Environment-based configuration. Paper trading by default; secrets are never repr'd."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .errors import ConfigError, LiveTradingNotAllowed

PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
LIVE_TRADING_URL = "https://api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    api_key_id: str = field(repr=False)
    api_secret_key: str = field(repr=False)
    live: bool = False
    data_plan: str = "basic"            # "basic" (IEX real-time, SIP >15 min old) or "sip" (Algo Trader Plus)
    options_feed: str = "indicative"    # "indicative" (free) or "opra"
    trading_url: str = PAPER_TRADING_URL
    data_url: str = DATA_URL
    timeout: float = 15.0
    max_retries: int = 4
    requests_per_minute: int = 190      # stay under Alpaca's 200/min
    sip_delay_minutes: int = 15

    @property
    def mode(self) -> str:
        return "live" if self.live else "paper"

    @classmethod
    def from_env(cls, *, allow_live: bool = False, **overrides) -> "Config":
        """Build config from env vars.

        Env: ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY (required);
        ALPACA_LIVE, ALPACA_DATA_PLAN, ALPACA_OPTIONS_FEED, ALPACA_TIMEOUT,
        ALPACA_MAX_RETRIES, ALPACA_RPM (optional).

        Live trading requires BOTH ALPACA_LIVE=true and allow_live=True; any
        mismatch raises LiveTradingNotAllowed rather than silently picking one.
        """
        key = overrides.pop("api_key_id", None) or os.environ.get("ALPACA_API_KEY_ID")
        sec = overrides.pop("api_secret_key", None) or os.environ.get("ALPACA_API_SECRET_KEY")
        if not key or not sec:
            raise ConfigError("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY are not set")
        env_live = _truthy(os.environ.get("ALPACA_LIVE"))
        live = resolve_live(env_live, allow_live)
        kw = dict(
            api_key_id=key,
            api_secret_key=sec,
            live=live,
            trading_url=LIVE_TRADING_URL if live else PAPER_TRADING_URL,
            data_plan=os.environ.get("ALPACA_DATA_PLAN", "basic").lower(),
            options_feed=os.environ.get("ALPACA_OPTIONS_FEED", "indicative").lower(),
            timeout=float(os.environ.get("ALPACA_TIMEOUT", 15)),
            max_retries=int(os.environ.get("ALPACA_MAX_RETRIES", 4)),
            requests_per_minute=int(os.environ.get("ALPACA_RPM", 190)),
        )
        kw.update(overrides)
        if kw.get("live") and not (env_live and allow_live):
            raise LiveTradingNotAllowed("live=True override requires ALPACA_LIVE=true and allow_live=True")
        return cls(**kw)


def resolve_live(env_live: bool, allow_live: bool) -> bool:
    if env_live and allow_live:
        return True
    if env_live and not allow_live:
        raise LiveTradingNotAllowed("ALPACA_LIVE=true but allow_live=True was not passed; refusing to guess")
    if allow_live and not env_live:
        raise LiveTradingNotAllowed("allow_live=True but ALPACA_LIVE is not 'true'; refusing live trading")
    return False
