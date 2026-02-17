from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Any
import httpx

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()


class TwelveDataError(RuntimeError):
    pass


class InvalidSymbolError(TwelveDataError):
    pass


@dataclass
class Candle:
    t: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None


def _ensure_key() -> None:
    if not TWELVEDATA_API_KEY:
        raise TwelveDataError("Missing TWELVEDATA_API_KEY")


def _normalize_dt(dt_str: str) -> str:
    t = dt_str.replace(" ", "T")
    if "Z" not in t and "+" not in t:
        t += "Z"
    return t


async def _call_time_series(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    outputsize: int,
) -> list[Candle]:
    _ensure_key()
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": str(int(outputsize)),
        "apikey": TWELVEDATA_API_KEY,
        "format": "JSON",
        "type": "candles",
    }
    r = await client.get(url, params=params, timeout=25)
    r.raise_for_status()
    data: Any = r.json()

    if isinstance(data, dict) and data.get("status") == "error":
        msg = str(data.get("message", "TwelveData error"))
        if "symbol" in msg.lower() and "invalid" in msg.lower():
            raise InvalidSymbolError(msg)
        raise TwelveDataError(msg)

    values = (data.get("values") or []) if isinstance(data, dict) else []
    out: list[Candle] = []
    for row in reversed(values):
        dt_str = row.get("datetime")
        if not dt_str:
            continue
        out.append(
            Candle(
                t=_normalize_dt(dt_str),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]) if row.get("volume") not in (None, "", "null") else None,
            )
        )
    return out


async def symbol_search(client: httpx.AsyncClient, query: str, limit: int = 25) -> list[dict]:
    _ensure_key()
    url = "https://api.twelvedata.com/symbol_search"
    params = {"symbol": query, "apikey": TWELVEDATA_API_KEY, "outputsize": str(int(limit))}
    r = await client.get(url, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("status") == "error":
        raise TwelveDataError(str(data.get("message", "TwelveData error")))
    return (data.get("data") or []) if isinstance(data, dict) else []


async def resolve_gold_symbol(client: httpx.AsyncClient, candidates: tuple[str, ...]) -> str:
    # 1) Try candidates first
    last_err: Exception | None = None
    for s in candidates:
        try:
            await _call_time_series(client, s, "1min", 10)
            return s
        except InvalidSymbolError as e:
            last_err = e
        except Exception as e:
            # other failures: keep trying candidates
            last_err = e

    # 2) Fall back to symbol_search for "XAU"
    try:
        rows = await symbol_search(client, "XAU", limit=50)
        # Prefer forex XAU/USD if present
        preferred = []
        for r in rows:
            sym = str(r.get("symbol") or "")
            name = str(r.get("instrument_name") or "").lower()
            typ = str(r.get("instrument_type") or "").lower()
            exch = str(r.get("exchange") or "").lower()

            score = 0
            if "xau" in sym.lower():
                score += 3
            if "usd" in sym.lower():
                score += 2
            if "/" in sym:
                score += 2
            if "gold" in name:
                score += 2
            if "forex" in typ or "fx" in typ:
                score += 2
            if exch in ("forex", "oanda", "fx"):
                score += 1

            preferred.append((score, sym))

        preferred.sort(reverse=True)
        if preferred and preferred[0][0] > 0:
            return preferred[0][1]
    except Exception as e:
        last_err = e

    raise InvalidSymbolError(f"Could not resolve a valid gold symbol. Last error: {last_err}")


async def fetch_time_series(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    outputsize: int,
) -> list[Candle]:
    # symbol passed in should already be resolved, but we keep this simple
    return await _call_time_series(client, symbol, interval, outputsize)
