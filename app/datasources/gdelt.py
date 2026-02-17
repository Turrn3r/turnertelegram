from __future__ import annotations
from dataclasses import dataclass
from typing import List
import httpx


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published: str
    relevance: float


def score_relevance(title: str) -> float:
    t = title.lower()
    score = 0.0
    if any(k in t for k in ["gold", "xau", "bullion", "safe haven"]):
        score += 0.35
    if any(k in t for k in ["cpi", "pce", "fomc", "fed", "powell", "rates", "yields", "treasury", "inflation", "dollar"]):
        score += 0.18
    if any(k in t for k in ["geopolitical", "war", "sanctions", "attack", "conflict", "crisis"]):
        score += 0.18
    return min(1.0, score)


async def fetch_gold_news(client: httpx.AsyncClient, max_items: int) -> List[NewsItem]:
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    query = '(gold OR XAU OR bullion OR "safe haven") (Fed OR CPI OR inflation OR yields OR dollar OR geopolitical OR war OR sanctions)'
    params = {"query": query, "mode": "ArtList", "format": "json", "maxrecords": str(int(max_items)), "sort": "HybridRel"}

    r = await client.get(url, params=params, timeout=25)
    r.raise_for_status()
    data = r.json()

    arts = (data.get("articles") or []) if isinstance(data, dict) else []
    out: List[NewsItem] = []
    for a in arts:
        title = a.get("title") or ""
        url_ = a.get("url") or ""
        src = a.get("sourceCountry") or (a.get("sourceCollection") or "GDELT")
        dt = a.get("seendate") or ""
        if not title or not url_:
            continue
        out.append(NewsItem(title=title, url=url_, source=str(src), published=str(dt), relevance=score_relevance(title)))

    out.sort(key=lambda x: x.relevance, reverse=True)
    return out
