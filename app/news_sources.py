# app/news_sources.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List
import re
import httpx


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: str
    relevance: float  # 0..1


def _score_relevance(text: str) -> float:
    t = text.lower()
    score = 0.0
    # gold relevance
    for k in ["gold", "xau", "bullion", "safe haven"]:
        if k in t:
            score += 0.25
    # macro relevance
    for k in ["cpi", "pce", "fomc", "fed", "powell", "rates", "yield", "treasury", "inflation", "dollar", "geopolitical"]:
        if k in t:
            score += 0.08
    return min(1.0, score)


async def fetch_gdelt_gold_news(client: httpx.AsyncClient, max_items: int = 10) -> List[NewsItem]:
    """
    GDELT 2.1 DOC API. Query tuned for gold macro catalysts.
    """
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    query = '(gold OR XAU OR bullion OR "safe haven") (Fed OR CPI OR inflation OR yields OR dollar OR geopolitical OR war OR sanctions)'
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(int(max_items)),
        "sort": "HybridRel",
    }
    r = await client.get(url, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()

    arts = (data.get("articles") or []) if isinstance(data, dict) else []
    items: List[NewsItem] = []
    for a in arts:
        title = a.get("title") or ""
        url_ = a.get("url") or ""
        src = a.get("sourceCountry") or (a.get("sourceCollection") or "GDELT")
        dt = a.get("seendate") or ""
        rel = _score_relevance(title)
        if title and url_:
            items.append(NewsItem(title=title, url=url_, source=str(src), published=str(dt), relevance=rel))
    # sort by relevance then most recent-ish
    items.sort(key=lambda x: x.relevance, reverse=True)
    return items
