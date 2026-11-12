"""HTTP transport: auth headers, timeouts, rate limiting, retries with backoff, error mapping."""
from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from typing import Any

import httpx

from .config import Config
from .errors import (AuthError, MarketClientError, NotFoundError, PlanLimitError,
                     ProviderUnavailableError, RateLimitError, ServerError, ValidationError)

log = logging.getLogger("alpaca_core.transport")

RETRY_STATUS = {429, 500, 502, 503, 504}
PLAN_LIMIT_MARKERS = ("subscription does not permit", "subscription does not allow", "not entitled")


class RateLimiter:
    """Sliding-window limiter: at most `per_minute` calls in any 60s window (thread-safe)."""

    def __init__(self, per_minute: int, window: float = 60.0, clock=time.monotonic, sleep=time.sleep):
        self.per_minute = max(1, per_minute)
        self.window = window
        self._clock, self._sleep = clock, sleep
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                while self._calls and now - self._calls[0] >= self.window:
                    self._calls.popleft()
                if len(self._calls) < self.per_minute:
                    self._calls.append(now)
                    return
                wait = self.window - (now - self._calls[0])
            self._sleep(max(wait, 0.01))


def _redact(params: dict | None) -> dict | None:
    return params  # params never contain secrets (auth is header-only); kept as a hook


class Transport:
    def __init__(self, config: Config, *, client: httpx.Client | None = None,
                 limiter: RateLimiter | None = None, sleep=time.sleep):
        self.config = config
        self._sleep = sleep
        self.limiter = limiter or RateLimiter(config.requests_per_minute)
        self._http = client or httpx.Client(timeout=config.timeout)
        self._headers = {
            "APCA-API-KEY-ID": config.api_key_id,
            "APCA-API-SECRET-KEY": config.api_secret_key,
            "Accept": "application/json",
        }

    def close(self) -> None:
        self._http.close()

    # -- public helpers -------------------------------------------------
    def trading(self, method: str, path: str, **kw) -> Any:
        return self.request(method, self.config.trading_url + path, **kw)

    def data(self, method: str, path: str, **kw) -> Any:
        return self.request(method, self.config.data_url + path, **kw)

    def request(self, method: str, url: str, *, params: dict | None = None,
                json: Any = None, retry_on_network: bool | None = None) -> Any:
        method = method.upper()
        params = {k: v for k, v in (params or {}).items() if v is not None}
        # Only idempotent calls are retried on network errors / 5xx (never re-send an order POST).
        if retry_on_network is None:
            retry_on_network = method in {"GET", "DELETE", "HEAD"}
        attempt = 0
        while True:
            attempt += 1
            self.limiter.acquire()
            try:
                resp = self._http.request(method, url, params=params, json=json, headers=self._headers)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if retry_on_network and attempt <= self.config.max_retries:
                    self._backoff(attempt, None)
                    continue
                raise ProviderUnavailableError(f"{method} {url} failed: {type(e).__name__}: {e}") from e

            if resp.status_code in RETRY_STATUS and attempt <= self.config.max_retries:
                # 429 means "not processed", so it is safe to retry any method.
                if resp.status_code == 429 or retry_on_network:
                    log.warning("%s %s -> %s, retry %d", method, url, resp.status_code, attempt)
                    self._backoff(attempt, resp.headers.get("Retry-After"))
                    continue
            return self._handle(resp, method, url)

    # -- internals ------------------------------------------------------
    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = None
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = None
        if delay is None:
            delay = min(30.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.25)
        self._sleep(delay)

    @staticmethod
    def _handle(resp: httpx.Response, method: str, url: str) -> Any:
        code = resp.status_code
        if 200 <= code < 300:
            if code == 204 or not resp.content:
                return None
            try:
                return resp.json()
            except ValueError:
                return resp.text
        body = resp.text[:500]
        try:
            msg = resp.json().get("message") or body
        except Exception:
            msg = body
        where = f"{method} {httpx.URL(url).path}"
        if code == 403 and any(m in body.lower() for m in PLAN_LIMIT_MARKERS):
            raise PlanLimitError(f"{where}: {msg}", status=code, body=body)
        if code in (401, 403):
            raise AuthError(f"{where}: {msg}", status=code, body=body)
        if code == 404:
            raise NotFoundError(f"{where}: {msg}", status=code, body=body)
        if code in (400, 422):
            raise ValidationError(f"{where}: {msg}", status=code, body=body)
        if code == 429:
            raise RateLimitError(f"{where}: rate limited", status=code, body=body)
        if code >= 500:
            raise ServerError(f"{where}: {code} {msg}", status=code, body=body)
        raise MarketClientError(f"{where}: {code} {msg}", status=code, body=body)
