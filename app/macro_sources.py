# app/macro_sources.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List
import os
import httpx


@dataclass
class MacroEvent:
    title: str
    country: str
    datetime: str
    importance: str
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None


TE_API_KEY = os.getenv("TRADING_ECONOMICS_KEY", "").strip()


async def fetch_macro_events(client: httpx.AsyncClient, max_items: int = 15) -> List[MacroEvent]:
    """
    Optional: TradingEconomics calendar.
    Requires TRADING_ECONOMICS_KEY.
    """
    if not TE_API_KEY:
        return []

    # TradingEconomics has multiple formats; this is a common pattern:
    url = "https://api.tradingeconomics.com/calendar"
    params = {"c": TE_API_KEY, "format": "json"}

    r = await client.get(url, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()

    events: List[MacroEvent] = []
    if isinstance(data, list):
        for e in data[:max_items]:
            events.append(MacroEvent(
                title=str(e.get("Event") or e.get("event") or ""),
                country=str(e.get("Country") or e.get("country") or ""),
                datetime=str(e.get("Date") or e.get("date") or ""),
                importance=str(e.get("Importance") or e.get("importance") or ""),
                actual=str(e.get("Actual") or e.get("actual") or "") or None,
                forecast=str(e.get("Forecast") or e.get("forecast") or "") or None,
                previous=str(e.get("Previous") or e.get("previous") or "") or None,
            ))
    return events
