"""Facade: one object exposing every service. Apps should only touch this + models + errors."""
from __future__ import annotations

from typing import Optional

from .config import Config
from .interfaces import (AccountService, CryptoDataService, MarketDataService, OptionsDataService,
                         TradingService, WatchlistService)
from .transport import Transport


def _alpaca_backend(transport: Transport, dry_run: bool):
    from .providers.alpaca import (AlpacaAccount, AlpacaCryptoData, AlpacaMarketData, AlpacaOptionsData,
                                   AlpacaTrading, AlpacaWatchlists)
    return dict(market=AlpacaMarketData(transport), options=AlpacaOptionsData(transport),
                crypto=AlpacaCryptoData(transport), account=AlpacaAccount(transport),
                trading=AlpacaTrading(transport, dry_run=dry_run), watchlists=AlpacaWatchlists(transport))


PROVIDERS = {"alpaca": _alpaca_backend}


class MarketClient:
    """Provider-agnostic entry point.

        client = MarketClient()                 # paper, env credentials
        client.market.latest_quote("SPY")
        client.trading.bracket(..., dry_run=True)

    Live trading needs BOTH env ALPACA_LIVE=true and MarketClient(allow_live=True).
    """

    market: MarketDataService
    options: OptionsDataService
    crypto: CryptoDataService
    account: AccountService
    trading: TradingService
    watchlists: WatchlistService

    def __init__(self, config: Optional[Config] = None, *, allow_live: bool = False, dry_run: bool = False,
                 provider: str = "alpaca", transport: Optional[Transport] = None, **config_overrides):
        self.config = config or Config.from_env(allow_live=allow_live, **config_overrides)
        if self.config.live and not allow_live:
            from .errors import LiveTradingNotAllowed
            raise LiveTradingNotAllowed("live config passed without allow_live=True")
        self.provider = provider
        self.transport = transport or Transport(self.config)
        for name, svc in PROVIDERS[provider](self.transport, dry_run).items():
            setattr(self, name, svc)

    @property
    def mode(self) -> str:
        return self.config.mode

    def close(self) -> None:
        self.transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __repr__(self) -> str:
        return f"MarketClient(provider={self.provider!r}, mode={self.mode!r}, data_plan={self.config.data_plan!r})"


AlpacaClient = MarketClient  # friendly alias
