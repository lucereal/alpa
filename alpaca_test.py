#!/usr/bin/env python3
"""Minimal Alpaca connection test (free/Basic plan). Reads keys from env; never prints them."""
import os, json, sys, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

KEY = os.environ.get("ALPACA_API_KEY_ID"); SEC = os.environ.get("ALPACA_API_SECRET_KEY")
if not KEY or not SEC:
    sys.exit("missing ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY")
H = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SEC, "Accept": "application/json"}

def get(url):
    req = urllib.request.Request(url, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:
        return None, str(e)[:200]

print("== account ==")
env = None
for name, base in [("paper", "https://paper-api.alpaca.markets"), ("live", "https://api.alpaca.markets")]:
    s, b = get(base + "/v2/account")
    if s == 200:
        env = (name, base)
        print(f"{name}: OK status={b.get('status')} equity={b.get('equity')} buying_power={b.get('buying_power')}")
    else:
        print(f"{name}: {s}")
if env:
    s, b = get(env[1] + "/v2/clock"); print("clock:", s, b if s == 200 else b)

D = "https://data.alpaca.markets/v2"
print("== latest quote/trade (IEX real-time) ==")
for sym in ["SPY", "NVDA"]:
    s, b = get(f"{D}/stocks/{sym}/quotes/latest?feed=iex"); print(sym, "quote", s, b.get("quote") if s == 200 else b)
    s, b = get(f"{D}/stocks/{sym}/trades/latest?feed=iex"); print(sym, "trade", s, b.get("trade") if s == 200 else b)

print("== historical daily bars, SIP (full market) ==")
end = (datetime.now(timezone.utc) - timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ")
start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
s, b = get(f"{D}/stocks/bars?symbols=SPY,NVDA&timeframe=1Day&start={start}&end={end}&feed=sip&adjustment=raw")
if s == 200:
    for sym, bars in b.get("bars", {}).items():
        for x in bars[-3:]:
            print(sym, x["t"][:10], "o", x["o"], "h", x["h"], "l", x["l"], "c", x["c"], "v", x["v"], "vw", x.get("vw"))
else:
    print("bars", s, b)

print("== SIP real-time probe (expected to fail on free plan) ==")
s, b = get(f"{D}/stocks/SPY/quotes/latest?feed=sip"); print("sip latest:", s, (b.get("quote") if s == 200 else b))
