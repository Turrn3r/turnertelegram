from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

# App symbols
SYMBOL_XRP = "XRPUSD"
SYMBOL_GOLD = "XAUUSD"
SYMBOL_SILVER = "XAGUSD"
SYMBOL_OIL = "USOIL"

# TwelveData symbols
TD_SYMBOLS = {
    SYMBOL_XRP: "XRP/USD",
    SYMBOL_GOLD: "XAU/USD",
    SYMBOL_SILVER: "XAG/USD",
    SYMBOL_OIL: "USOIL",
}

SUPPORTED_INTERVALS = {"1min", "5min", "15min", "30min", "45min", "1h", "2h", "4h", "1day"}


@dataclass
class Candle:
    t: str  # ISO-like timestamp
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None


def assert_configured() -> None:
    if not TWELVEDATA_API_KEY:
        raise RuntimeError("Missing TWELVEDATA_API_KEY (set as Fly secret)")


async def fetch_time_series(
    client: httpx.AsyncClient,
    app_symbol: str,
    interval: str = "15min",
    outputsize: int = 300,
) -> list[Candle]:
    assert_configured()
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported interval '{interval}'")
    td_symbol = TD_SYMBOLS.get(app_symbol)
    if not td_symbol:
        raise ValueError(f"Unknown symbol '{app_symbol}'")

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

    if data.get("status") == "error":
        raise RuntimeError(f"TwelveData error: {data.get('message', 'unknown')}")

    values = data.get("values") or []
    candles: list[Candle] = []
    for row in reversed(values):  # oldest->newest
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
