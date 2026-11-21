"""alpaca_core - provider-agnostic market data & trading client (Alpaca backend).

    from alpaca_core import MarketClient
    with MarketClient() as c:
        print(c.market.latest_quote("SPY"))
"""
from .client import AlpacaClient, MarketClient
from .config import Config
from .errors import (AuthError, ConfigError, LiveTradingNotAllowed, MarketClientError, NotFound, NotFoundError,
                     PlanLimitError, ProviderUnavailableError, RateLimitError, ServerError, ValidationError)
from .models import *  # noqa: F401,F403
from .models import to_dict

__version__ = "0.1.0"
