from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger("turnertelegram.twelvedata")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

SYMBOL_XRP = "XRPUSD"
SYMBOL_GOLD = "XAUUSD"
SYMBOL_SILVER = "XAGUSD"
SYMBOL_OIL = "USOIL"

# User-configurable “preferred” symbols (can be blank; we’ll resolve if needed)
TD_SYMBOLS = {
    SYMBOL_XRP: os.getenv("TD_XRP", "XRP/USD").strip(),
    SYMBOL_GOLD: os.getenv("TD_XAU", "XAU/USD").strip(),
    SYMBOL_SILVER: os.getenv("TD_XAG", "XAG/USD").strip(),
    SYMBOL_OIL: os.getenv("TD_OIL", "USOIL").strip(),
}

# Fallback search queries (what we search for if preferred symbol fails)
SEARCH_QUERIES = {
    SYMBOL_XRP: ["XRP/USD", "XRPUSD", "XRP"],
    SYMBOL_GOLD: ["XAU/USD", "XAUUSD", "Gold"],
    SYMBOL_SILVER: ["XAG/USD", "XAGUSD", "Silver"],
    SYMBOL_OIL: ["WTI", "Brent", "Crude Oil", "Oil", "USOIL"],
}

SUPPORTED_INTERVALS = {"1min", "5min", "15min", "30min", "45min", "1h", "2h", "4h", "1day"}

# simple in-memory cache: app_symbol -> resolved TwelveData symbol string
_RESOLVED: dict[str, str] = {}


@dataclass
class Candle:
    t: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None


def assert_configured() -> None:
    if not TWELVEDATA_API_KEY:
        raise RuntimeError("Missing TWELVEDATA_API_KEY (set as Fly secret)")


async def symbol_search(client: httpx.AsyncClient, query: str) -> list[dict]:
    url = "https://api.twelvedata.com/symbol_search"
    params = {"symbol": query, "apikey": TWELVEDATA_API_KEY, "outputsize": "30"}
    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    # data can be {"data":[...]} or error
    if isinstance(data, dict) and data.get("status") == "error":
        return []
    return (data.get("data") or []) if isinstance(data, dict) else []


def pick_best_symbol(results: list[dict]) -> Optional[str]:
    """
    Heuristic: prefer entries that include '/' (fx-style), then those with 'USD' in symbol,
    and with type 'Forex'/'Commodities'/'Crypto' where present.
    """
    if not results:
        return None

    def score(r: dict) -> int:
        s = (r.get("symbol") or "").upper()
        t = (r.get("instrument_type") or "").lower()
        sc = 0
        if "/" in s:
            sc += 5
        if "USD" in s:
            sc += 4
        if "forex" in t:
            sc += 3
        if "commodity" in t or "commodities" in t:
            sc += 3
        if "crypto" in t:
            sc += 2
        if "spot" in t:
            sc += 1
        return sc

    results_sorted = sorted(results, key=score, reverse=True)
    best = results_sorted[0].get("symbol")
    return best.strip() if best else None


async def resolve_symbol(client: httpx.AsyncClient, app_symbol: str) -> str:
    # cached?
    if app_symbol in _RESOLVED:
        return _RESOLVED[app_symbol]

    preferred = TD_SYMBOLS.get(app_symbol, "").strip()
    if preferred:
        _RESOLVED[app_symbol] = preferred
        return preferred

    # no preferred -> search
    for q in SEARCH_QUERIES.get(app_symbol, [app_symbol]):
        res = await symbol_search(client, q)
        best = pick_best_symbol(res)
        if best:
            _RESOLVED[app_symbol] = best
            log.warning("Resolved %s via search '%s' -> %s", app_symbol, q, best)
            return best

    raise RuntimeError(f"Could not resolve a TwelveData symbol for {app_symbol}")


async def fetch_time_series(
    client: httpx.AsyncClient,
    app_symbol: str,
    interval: str = "1min",
    outputsize: int = 300,
) -> list[Candle]:
    assert_configured()
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported interval '{interval}'")

    # resolve first
    td_symbol = await resolve_symbol(client, app_symbol)
    if not td_symbol:
        raise RuntimeError(f"Resolved symbol empty for {app_symbol}")

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": td_symbol,
        "interval": interval,
        "outputsize": str(int(outputsize)),
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
        "type": "candles",
    }

    r = await client.get(url, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()

    # If invalid, try auto-research once (common for OIL)
    if isinstance(data, dict) and data.get("status") == "error":
        msg = (data.get("message") or "").lower()
        if "symbol" in msg and ("missing" in msg or "invalid" in msg):
            log.warning("Symbol rejected by TwelveData: app=%s td_symbol=%s msg=%s", app_symbol, td_symbol, data.get("message"))
            # purge cached and re-resolve via symbol_search
            _RESOLVED.pop(app_symbol, None)
            TD_SYMBOLS[app_symbol] = ""  # force search path
            td_symbol = await resolve_symbol(client, app_symbol)

            params["symbol"] = td_symbol
            r2 = await client.get(url, params=params, timeout=25)
            r2.raise_for_status()
            data = r2.json()

        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(f"TwelveData error for {td_symbol}: {data.get('message', 'unknown')}")

    values = data.get("values") or []
    candles: list[Candle] = []
    for row in reversed(values):
        dt_str = row.get("datetime")
        if not dt_str:
            continue
        t = dt_str.replace(" ", "T")
        if "Z" not in t and "+" not in t:
            t = t + "Z"
        candles.append(
            Candle(
                t=t,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]) if row.get("volume") not in (None, "", "null") else None,
            )
        )

    return candles
