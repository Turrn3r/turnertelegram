# app/twelvedata.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import httpx

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()


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
        raise RuntimeError("Missing TWELVEDATA_API_KEY")


async def fetch_time_series(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str = "1min",
    outputsize: int = 720,
) -> list[Candle]:
    assert_configured()
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,          # "XAU/USD" works too; but we’ll use "XAUUSD" in config
        "interval": interval,
        "outputsize": str(int(outputsize)),
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
        "type": "candles",
    }
    r = await client.get(url, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()

    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(data.get("message", "TwelveData error"))

    values = (data.get("values") or []) if isinstance(data, dict) else []
    candles: list[Candle] = []
    for row in reversed(values):
        dt_str = row.get("datetime")
        if not dt_str:
            continue
        t = dt_str.replace(" ", "T")
        if "Z" not in t and "+" not in t:
            t += "Z"
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
