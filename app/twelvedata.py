from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import httpx

log = logging.getLogger("turnertelegram.twelvedata")

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

SYMBOL_XRP = "XRPUSD"
SYMBOL_GOLD = "XAUUSD"
SYMBOL_SILVER = "XAGUSD"
SYMBOL_OIL = "USOIL"

# Preferred symbols (optional overrides)
TD_SYMBOLS = {
    SYMBOL_XRP: os.getenv("TD_XRP", "XRP/USD").strip(),
    SYMBOL_GOLD: os.getenv("TD_XAU", "XAU/USD").strip(),
    SYMBOL_SILVER: os.getenv("TD_XAG", "XAG/USD").strip(),
    SYMBOL_OIL: os.getenv("TD_OIL", "USOIL").strip(),
}

# Optional preferred exchanges (helps oil/futures)
TD_EXCHANGES = {
    SYMBOL_XRP: os.getenv("TD_XRP_EXCHANGE", "").strip(),
    SYMBOL_GOLD: os.getenv("TD_XAU_EXCHANGE", "").strip(),
    SYMBOL_SILVER: os.getenv("TD_XAG_EXCHANGE", "").strip(),
    SYMBOL_OIL: os.getenv("TD_OIL_EXCHANGE", "").strip(),
}

# Search queries if preferred fails
SEARCH_QUERIES = {
    SYMBOL_XRP: ["XRP/USD", "XRPUSD", "XRP"],
    SYMBOL_GOLD: ["XAU/USD", "XAUUSD", "Gold"],
    SYMBOL_SILVER: ["XAG/USD", "XAGUSD", "Silver"],
    SYMBOL_OIL: ["WTI", "Crude Oil", "Brent", "CL", "BZ", "USOIL", "OIL"],
}

SUPPORTED_INTERVALS = {"1min", "5min", "15min", "30min", "45min", "1h", "2h", "4h", "1day"}

# cache: app_symbol -> (symbol, exchange)
_RESOLVED: dict[str, Tuple[str, str]] = {}


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
    params = {"symbol": query, "apikey": TWELVEDATA_API_KEY, "outputsize": "50"}
    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("status") == "error":
        return []
    return (data.get("data") or []) if isinstance(data, dict) else []


def pick_best_instrument(app_symbol: str, results: list[dict]) -> Optional[Tuple[str, str]]:
    """
    Returns (symbol, exchange). Oil often needs exchange to disambiguate.
    Heuristic: prefer USD-quoted, spot/cfd/forex-like for metals, and known futures exchanges for oil.
    """
    if not results:
        return None

    def score(r: dict) -> int:
        sym = (r.get("symbol") or "").upper()
        exc = (r.get("exchange") or "").upper()
        itype = (r.get("instrument_type") or "").lower()
        name = (r.get("instrument_name") or "").lower()

        sc = 0

        # USD-ish preference
        if "USD" in sym or "/USD" in sym:
            sc += 6
        if "/" in sym:
            sc += 2

        # Instrument-type preferences
        if "forex" in itype:
            sc += 3
        if "commod" in itype:
            sc += 3
        if "crypto" in itype:
            sc += 2

        # Oil: prefer common crude instruments / exchanges
        if app_symbol == SYMBOL_OIL:
            if "wti" in name or "crude" in name:
                sc += 5
            if "brent" in name:
                sc += 3
            if exc in {"NYMEX", "ICE", "CME"}:
                sc += 5
            # Some CFDs/indices may be easier than futures
            if "cfd" in name:
                sc += 2

        # Metals: prefer XAU/XAG spot
        if app_symbol in {SYMBOL_GOLD, SYMBOL_SILVER}:
            if "spot" in name:
                sc += 3

        return sc

    best = sorted(results, key=score, reverse=True)[0]
    symbol = (best.get("symbol") or "").strip()
    exchange = (best.get("exchange") or "").strip()
    if not symbol:
        return None
    return symbol, exchange


async def resolve_instrument(client: httpx.AsyncClient, app_symbol: str) -> Tuple[str, str]:
    # cached?
    if app_symbol in _RESOLVED:
        return _RESOLVED[app_symbol]

    preferred_symbol = TD_SYMBOLS.get(app_symbol, "").strip()
    preferred_exchange = TD_EXCHANGES.get(app_symbol, "").strip()

    if preferred_symbol:
        _RESOLVED[app_symbol] = (preferred_symbol, preferred_exchange)
        return preferred_symbol, preferred_exchange

    # search fallback
    for q in SEARCH_QUERIES.get(app_symbol, [app_symbol]):
        res = await symbol_search(client, q)
        picked = pick_best_instrument(app_symbol, res)
        if picked:
            _RESOLVED[app_symbol] = picked
            log.warning("Resolved %s via search '%s' -> symbol=%s exchange=%s", app_symbol, q, picked[0], picked[1])
            return picked

    raise RuntimeError(f"Could not resolve a TwelveData instrument for {app_symbol}")


async def fetch_time_series(
    client: httpx.AsyncClient,
    app_symbol: str,
    interval: str = "1min",
    outputsize: int = 300,
) -> list[Candle]:
    assert_configured()
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported interval '{interval}'")

    symbol, exchange = await resolve_instrument(client, app_symbol)
    if not symbol:
        raise RuntimeError(f"Resolved symbol empty for {app_symbol}")

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": str(int(outputsize)),
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
        "type": "candles",
    }

    # This is the key fix for oil/futures
    if exchange:
        params["exchange"] = exchange

    r = await client.get(url, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()

    # If rejected, force re-resolve once using search (common for oil instruments)
    if isinstance(data, dict) and data.get("status") == "error":
        msg = (data.get("message") or "").lower()
        if "symbol" in msg and ("missing" in msg or "invalid" in msg):
            log.warning("Rejected instrument: app=%s symbol=%s exchange=%s msg=%s", app_symbol, symbol, exchange, data.get("message"))
            _RESOLVED.pop(app_symbol, None)
            TD_SYMBOLS[app_symbol] = ""
            TD_EXCHANGES[app_symbol] = ""
            symbol, exchange = await resolve_instrument(client, app_symbol)
            params["symbol"] = symbol
            if exchange:
                params["exchange"] = exchange
            else:
                params.pop("exchange", None)

            r2 = await client.get(url, params=params, timeout=25)
            r2.raise_for_status()
            data = r2.json()

        if isinstance(data, dict) and data.get("status") == "error":
            raise RuntimeError(f"TwelveData error for {symbol} ({exchange or 'no-exchange'}): {data.get('message', 'unknown')}")

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
