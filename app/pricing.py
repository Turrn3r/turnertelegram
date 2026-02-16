import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx

# Canonical symbols in your app
SYMBOL_XRP = "XRPUSD"
SYMBOL_GOLD = "GC.F"     # Gold futures (COMEX) on Stooq :contentReference[oaicite:1]{index=1}
SYMBOL_SILVER = "SI.F"   # Silver futures (COMEX) on Stooq :contentReference[oaicite:2]{index=2}
SYMBOL_OIL = "CL.F"      # WTI crude futures on Stooq

BINANCE_SYMBOL = "XRPUSDT"

@dataclass
class Quote:
    symbol: str
    price: float
    source: str

async def fetch_xrp_last(client: httpx.AsyncClient) -> Quote:
    # Binance last price for XRPUSDT (close-to-live)
    url = "https://api.binance.com/api/v3/ticker/price"
    params = {"symbol": BINANCE_SYMBOL}
    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    price = float(data["price"])
    return Quote(symbol=SYMBOL_XRP, price=price, source="binance")

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

    return Quote(symbol=stooq_symbol.upper(), price=float(last), source="stooq")

async def fetch_all() -> list[Quote]:
    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/1.0"}) as client:
        tasks = [
            fetch_xrp_last(client),
            fetch_stooq_last(client, SYMBOL_GOLD),
            fetch_stooq_last(client, SYMBOL_SILVER),
            fetch_stooq_last(client, SYMBOL_OIL),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    quotes: list[Quote] = []
    for r in results:
        if isinstance(r, Exception) or r is None:
            continue
        quotes.append(r)

    return quotes
