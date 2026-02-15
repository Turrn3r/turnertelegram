import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx

# Canonical symbols used everywhere in DB/UI
SYMBOL_XRP = "XRPUSD"
SYMBOL_GOLD = "XAUUSD"
SYMBOL_SILVER = "XAGUSD"
SYMBOL_OIL = "CL.F"   # Stooq uses CL.F for WTI crude futures

@dataclass
class Quote:
    symbol: str
    price: float
    source: str


async def fetch_xrp_usd(client: httpx.AsyncClient) -> Quote:
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "ripple", "vs_currencies": "usd"}
    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    price = float(data["ripple"]["usd"])
    return Quote(symbol=SYMBOL_XRP, price=price, source="coingecko")


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

    # Force canonical storage key (prevents mismatches)
    canonical = stooq_symbol.upper()
    return Quote(symbol=canonical, price=float(last), source="stooq")


async def fetch_all() -> list[Quote]:
    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/1.0"}) as client:
        tasks = [
            fetch_xrp_usd(client),
            fetch_stooq_last(client, SYMBOL_GOLD),
            fetch_stooq_last(client, SYMBOL_SILVER),
            fetch_stooq_last(client, SYMBOL_OIL),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    quotes: list[Quote] = []
    for r in results:
        if isinstance(r, Exception):
            continue
        if r is None:
            continue
        quotes.append(r)
    return quotes
