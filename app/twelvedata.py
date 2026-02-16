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
    # IMPORTANT: do NOT default to "WTI" here because symbol_search shows it's ambiguous (stock/ETF).
    # We'll probe proper crude candidates below.
    SYMBOL_OIL: os.getenv("TD_OIL", "").strip(),
}

# Optional preferred exchanges (helps oil/futures)
TD_EXCHANGES = {
    SYMBOL_XRP: os.getenv("TD_XRP_EXCHANGE", "").strip(),
    SYMBOL_GOLD: os.getenv("TD_XAU_EXCHANGE", "").strip(),
    SYMBOL_SILVER: os.getenv("TD_XAG_EXCHANGE", "").strip(),
    SYMBOL_OIL: os.getenv("TD_OIL_EXCHANGE", "").strip(),
}

# Search queries if preferred fails (NOT used for OIL)
SEARCH_QUERIES = {
    SYMBOL_XRP: ["XRP/USD", "XRPUSD", "XRP"],
    SYMBOL_GOLD: ["XAU/USD", "XAUUSD", "Gold"],
    SYMBOL_SILVER: ["XAG/USD", "XAGUSD", "Silver"],
}

SUPPORTED_INTERVALS = {"1min", "5min", "15min", "30min", "45min", "1h", "2h", "4h", "1day"}

# cache: app_symbol -> (symbol, exchange)
_RESOLVED: dict[str, Tuple[str, str]] = {}

# Oil candidates to probe (first success wins)
# Note: Many plans expose crude as futures/indices; these are common TwelveData mappings.
OIL_CANDIDATES = [
    {"symbol": "CL", "exchange": "NYMEX"},      # WTI crude futures (common)
    {"symbol": "CL1!", "exchange": ""},         # continuous (if available)
    {"symbol": "WTI", "exchange": ""},          # crude index/CFD on some plans (but ambiguous in symbol_search)
    {"symbol": "USOIL", "exchange": ""},        # CFD-style on some plans
    {"symbol": "BRENT", "exchange": "ICE"},     # Brent
    {"symbol": "BRN", "exchange": "ICE"},       # Brent alternative
    {"symbol": "UKOIL", "exchange": ""},        # CFD-style on some plans
]


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


def pick_best_instrument(results: list[dict]) -> Optional[Tuple[str, str]]:
    """
    Returns (symbol, exchange). Used for XRP/XAU/XAG only.
    """
    if not results:
        return None

    def score(r: dict) -> int:
        sym = (r.get("symbol") or "").upper()
        itype = (r.get("instrument_type") or "").lower()
        name = (r.get("instrument_name") or "").lower()
        sc = 0
        if "/USD" in sym or "USD" in sym:
            sc += 6
        if "/" in sym:
            sc += 2
        if "forex" in itype:
            sc += 3
        if "commod" in itype:
            sc += 3
        if "crypto" in itype:
            sc += 2
        # prefer spot-like names for metals
        if "spot" in name:
            sc += 2
        return sc

    best = sorted(results, key=score, reverse=True)[0]
    symbol = (best.get("symbol") or "").strip()
    exchange = (best.get("exchange") or "").strip()
    if not symbol:
        return None
    return symbol, exchange


async def _probe_time_series(client: httpx.AsyncClient, symbol: str, exchange: str, interval: str) -> tuple[bool, str]:
    """
    Returns (ok, message). If ok==True, candle data exists. If False, message holds error or reason.
    """
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": "5",
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
        "type": "candles",
    }
    if exchange:
        params["exchange"] = exchange

    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    if isinstance(data, dict) and data.get("status") == "error":
        return False, str(data.get("message", "unknown error"))
    vals = data.get("values") if isinstance(data, dict) else None
    if not vals:
        return False, "no values returned"
    return True, "ok"


async def resolve_instrument(client: httpx.AsyncClient, app_symbol: str) -> Tuple[str, str]:
    # cached?
    if app_symbol in _RESOLVED:
        return _RESOLVED[app_symbol]

    # user forced?
    preferred_symbol = TD_SYMBOLS.get(app_symbol, "").strip()
    preferred_exchange = TD_EXCHANGES.get(app_symbol, "").strip()
    if preferred_symbol:
        _RESOLVED[app_symbol] = (preferred_symbol, preferred_exchange)
        return preferred_symbol, preferred_exchange

    # OIL: DO NOT use symbol_search (WTI returns stocks/ETFs in your account)
    if app_symbol == SYMBOL_OIL:
        last_err = None
        for cand in OIL_CANDIDATES:
            sym = cand["symbol"]
            exc = cand["exchange"]
            # try 1m first (your requirement), then fallback ladder
            for iv in ("1min", "5min", "15min"):
                try:
                    ok, msg = await _probe_time_series(client, sym, exc, iv)
                    if ok:
                        _RESOLVED[app_symbol] = (sym, exc)
                        log.warning("Resolved OIL via probe -> symbol=%s exchange=%s interval_ok=%s", sym, exc, iv)
                        return sym, exc
                    last_err = f"{sym}({exc or '-'}) {iv}: {msg}"
                except Exception as e:
                    last_err = f"{sym}({exc or '-'}) {iv}: {e}"
                    continue

        raise RuntimeError(
            "Could not resolve OIL instrument via TwelveData time_series. "
            "This usually means crude/futures/CFDs are not enabled on your plan/key, "
            f"or the correct symbol/exchange differs. Last error: {last_err}"
        )

    # normal path for XRP/XAU/XAG
    for q in SEARCH_QUERIES.get(app_symbol, [app_symbol]):
        res = await symbol_search(client, q)
        picked = pick_best_instrument(res)
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

    # For OIL, if 1min isn't supported by the resolved instrument, caller may still request 1min.
    # We'll do a small fallback ladder here too, to keep charts flowing.
    intervals_to_try = [interval]
    if app_symbol == SYMBOL_OIL and interval == "1min":
        intervals_to_try += ["5min", "15min"]

    last_err = None
    for iv in intervals_to_try:
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": iv,
            "outputsize": str(int(outputsize)),
            "apikey": TWELVEDATA_API_KEY,
            "format": "JSON",
            "type": "candles",
        }
        if exchange:
            params["exchange"] = exchange

        r = await client.get(url, params=params, timeout=25)
        r.raise_for_status()
        data = r.json()

        if isinstance(data, dict) and data.get("status") == "error":
            last_err = data.get("message", "unknown")
            continue

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

    raise RuntimeError(f"TwelveData error for {symbol} ({exchange or 'no-exchange'}): {last_err}")
