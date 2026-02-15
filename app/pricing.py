import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx
from datetime import datetime, timezone

SYMBOL_XRP = "XRPUSD"
SYMBOL_GOLD = "XAUUSD"
SYMBOL_SILVER = "XAGUSD"
SYMBOL_OIL = "CL.F"

BINANCE_SYMBOL = "XRPUSDT"   # best liquidity
BINANCE_INTERVAL = "15m"

@dataclass
class Quote:
    symbol: str
    price: float
    source: str

@dataclass
class Candle:
    symbol: str
    interval: str
    open_time_utc: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str


async def fetch_xrp_usd(client: httpx.AsyncClient) -> Quote:
    # CoinGecko last price
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "ripple", "vs_currencies": "usd"}
    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    price = float(data["ripple"]["usd"])
    return Quote(symbol=SYMBOL_XRP, price=price, source="coingecko")


async def fetch_xrp_ohlc_15m(client: httpx.AsyncClient, limit: int = 200) -> list[Candle]:
    # Binance klines for real candlesticks (public)
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": BINANCE_SYMBOL, "interval": BINANCE_INTERVAL, "limit": str(limit)}
    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    candles: list[Candle] = []
    for k in data:
        # kline format: [openTime, open, high, low, close, volume, closeTime, ...]
        open_ms = int(k[0])
        open_time = datetime.fromtimestamp(open_ms / 1000.0, tz=timezone.utc).isoformat()
        candles.append(
            Candle(
                symbol=SYMBOL_XRP,
                interval=BINANCE_INTERVAL,
                open_time_utc=open_time,
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
                source="binance",
            )
        )
    return candles


async def fetch_stooq_last(client: httpx.AsyncClient, stooq_symbol: str) -> Optional[Quote]:
    url = "https://stooq.com/q/l/"
    params = {"s": stooq_symbol.lower(), "f": "sd2t2l", "h": "", "e": "csv"}
    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()

    lines = r.text.strip().splitlines()
    if len(lines) < 2:
        return None

    row = lines[1].split(",")
    if len(row) < 4:
        return None

    last = row[3].strip()
    if last in ("", "N/A"):
        return None

    canonical = stooq_symbol.upper()
    return Quote(symbol=canonical, price=float(last), source="stooq")


async def fetch_all() -> tuple[list[Quote], list[Candle]]:
    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/1.0"}) as client:
        tasks = [
            fetch_xrp_usd(client),
            fetch_stooq_last(client, SYMBOL_GOLD),
            fetch_stooq_last(client, SYMBOL_SILVER),
            fetch_stooq_last(client, SYMBOL_OIL),
        ]
        candles_task = fetch_xrp_ohlc_15m(client, limit=200)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        candles_res = await asyncio.gather(candles_task, return_exceptions=True)

    quotes: list[Quote] = []
    for r in results:
        if isinstance(r, Exception) or r is None:
            continue
        quotes.append(r)

    candles: list[Candle] = []
    if candles_res and not isinstance(candles_res[0], Exception):
        candles = candles_res[0]

    return quotes, candles
