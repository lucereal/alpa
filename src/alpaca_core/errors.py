"""Provider-agnostic exception hierarchy."""
from __future__ import annotations


class MarketClientError(Exception):
    """Base class for every error raised by alpaca_core."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


class ConfigError(MarketClientError):
    """Missing / invalid configuration (e.g. no credentials)."""


class AuthError(MarketClientError):
    """Credentials rejected (401/403 that is not a plan limit)."""


class PlanLimitError(AuthError):
    """Data subscription does not cover the request (e.g. recent SIP on the free plan)."""


class RateLimitError(MarketClientError):
    """429 after all retries were exhausted."""


class NotFoundError(MarketClientError):
    """404 - unknown symbol, order id, watchlist, etc."""


NotFound = NotFoundError  # short alias


class ValidationError(MarketClientError):
    """400/422 from the provider, or a request we refused to send."""


class ServerError(MarketClientError):
    """5xx after all retries were exhausted."""


class ProviderUnavailableError(MarketClientError):
    """Network failure / timeout - provider unreachable after retries."""


class LiveTradingNotAllowed(MarketClientError):
    """Live trading was requested without BOTH ALPACA_LIVE=true and allow_live=True."""
