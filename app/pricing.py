import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

SYMBOL_XRP = "XRPUSD"
SYMBOL_GOLD = "GC.F"
SYMBOL_SILVER = "SI.F"
SYMBOL_OIL = "CL.F"

BINANCE_SYMBOL = "XRPUSDT"
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")

TWELVEDATA_SYMBOLS = {
    SYMBOL_XRP: "XRP/USD",
    SYMBOL_GOLD: "XAU/USD",
    SYMBOL_SILVER: "XAG/USD",
    SYMBOL_OIL: "USOIL",
}


@dataclass
class Quote:
    symbol: str
    price: float
    source: str


@dataclass
class FlowEvent:
    event_id: str
    symbol: str
    side: str
    price: float
    quantity: float
    notional_usd: float
    source: str
    ts_utc: datetime


async def fetch_xrp_last(client: httpx.AsyncClient) -> Quote:
    url = "https://api.binance.com/api/v3/ticker/price"
    params = {"symbol": BINANCE_SYMBOL}
    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    return Quote(symbol=SYMBOL_XRP, price=float(data["price"]), source="binance")


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


async def fetch_twelvedata_last(client: httpx.AsyncClient, app_symbol: str) -> Optional[Quote]:
    if not TWELVEDATA_API_KEY:
        return None

    td_symbol = TWELVEDATA_SYMBOLS.get(app_symbol)
    if not td_symbol:
        return None

    url = "https://api.twelvedata.com/price"
    params = {"symbol": td_symbol, "apikey": TWELVEDATA_API_KEY}
    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    if "price" not in data:
        return None
    return Quote(symbol=app_symbol, price=float(data["price"]), source="twelvedata")


async def fetch_large_xrp_trades(client: httpx.AsyncClient, min_notional_usd: float = 250_000.0) -> list[FlowEvent]:
    url = "https://api.binance.com/api/v3/aggTrades"
    params = {"symbol": BINANCE_SYMBOL, "limit": 200}
    r = await client.get(url, params=params, timeout=20)
    r.raise_for_status()
    rows = r.json()

    events: list[FlowEvent] = []
    for row in rows:
        px = float(row["p"])
        qty = float(row["q"])
        notional = px * qty
        if notional < min_notional_usd:
            continue

        # m=True means buyer is maker -> typically interpreted as sell-initiated
        side = "SELL" if bool(row.get("m", False)) else "BUY"
        ts_utc = datetime.fromtimestamp(int(row["T"]) / 1000.0, tz=timezone.utc)
        event_id = f"binance-{row['a']}"

        events.append(
            FlowEvent(
                event_id=event_id,
                symbol=SYMBOL_XRP,
                side=side,
                price=px,
                quantity=qty,
                notional_usd=notional,
                source="binance_agg_trade",
                ts_utc=ts_utc,
            )
        )

    events.sort(key=lambda e: e.ts_utc)
    return events


async def fetch_all() -> list[Quote]:
    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/1.0"}) as client:
        # Try TwelveData for all markets first
        td_syms = [SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER, SYMBOL_OIL]
        td_tasks = [fetch_twelvedata_last(client, sym) for sym in td_syms]
        td_results = await asyncio.gather(*td_tasks, return_exceptions=True)

        quotes_by_symbol: dict[str, Quote] = {}
        for result in td_results:
            if isinstance(result, Exception) or result is None:
                continue
            quotes_by_symbol[result.symbol] = result

        # Fallbacks
        fallback_tasks = []
        if SYMBOL_XRP not in quotes_by_symbol:
            fallback_tasks.append(fetch_xrp_last(client))
        if SYMBOL_GOLD not in quotes_by_symbol:
            fallback_tasks.append(fetch_stooq_last(client, SYMBOL_GOLD))
        if SYMBOL_SILVER not in quotes_by_symbol:
            fallback_tasks.append(fetch_stooq_last(client, SYMBOL_SILVER))
        if SYMBOL_OIL not in quotes_by_symbol:
            fallback_tasks.append(fetch_stooq_last(client, SYMBOL_OIL))

        if fallback_tasks:
            fallback_results = await asyncio.gather(*fallback_tasks, return_exceptions=True)
            for result in fallback_results:
                if isinstance(result, Exception) or result is None:
                    continue
                quotes_by_symbol[result.symbol] = result

    ordered = [SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER, SYMBOL_OIL]
    return [quotes_by_symbol[s] for s in ordered if s in quotes_by_symbol]
