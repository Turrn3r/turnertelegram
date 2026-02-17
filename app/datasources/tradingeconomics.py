from __future__ import annotations
import os
from dataclasses import dataclass
from typing import List
import httpx

TE_API_KEY = os.getenv("TRADING_ECONOMICS_KEY", "").strip()


@dataclass
class MacroEvent:
    title: str
    country: str
    datetime: str
    importance: str
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None


async def fetch_calendar(client: httpx.AsyncClient, max_items: int = 20) -> List[MacroEvent]:
    if not TE_API_KEY:
        return []

    url = "https://api.tradingeconomics.com/calendar"
    params = {"c": TE_API_KEY, "format": "json"}

    r = await client.get(url, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()

    out: List[MacroEvent] = []
    if isinstance(data, list):
        for e in data[:max_items]:
            out.append(
                MacroEvent(
                    title=str(e.get("Event") or e.get("event") or ""),
                    country=str(e.get("Country") or e.get("country") or ""),
                    datetime=str(e.get("Date") or e.get("date") or ""),
                    importance=str(e.get("Importance") or e.get("importance") or ""),
                    actual=str(e.get("Actual") or e.get("actual") or "") or None,
                    forecast=str(e.get("Forecast") or e.get("forecast") or "") or None,
                    previous=str(e.get("Previous") or e.get("previous") or "") or None,
                )
            )
    return out
