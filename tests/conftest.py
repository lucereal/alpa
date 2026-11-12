import pytest

from alpaca_core import Config, MarketClient
from alpaca_core.transport import RateLimiter, Transport


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.delenv("ALPACA_LIVE", raising=False)
    return Config(api_key_id="TESTKEY", api_secret_key="TESTSECRET", max_retries=3)


@pytest.fixture
def client(cfg):
    t = Transport(cfg, sleep=lambda s: None)
    c = MarketClient(cfg, transport=t)
    yield c
    c.close()


TRADING = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
