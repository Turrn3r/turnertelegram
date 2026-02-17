from __future__ import annotations
from typing import Any

BULL_WORDS = ["safe haven", "geopolitical", "conflict", "war", "sanctions", "crisis", "inflation"]
BEAR_WORDS = ["hawkish", "rate hike", "yields rise", "strong dollar", "dollar strength", "tightening"]


def news_bias(news_titles: list[str]) -> tuple[str, float]:
    txt = " ".join(news_titles).lower()
    bull = sum(1 for w in BULL_WORDS if w in txt)
    bear = sum(1 for w in BEAR_WORDS if w in txt)
    total = bull + bear
    score = min(1.0, total / 8.0) if total else 0.0
    if bull > bear:
        return "BULL", score
    if bear > bull:
        return "BEAR", score
    return "NEUTRAL", score


def macro_relevance(events: list[dict[str, Any]]) -> float:
    if not events:
        return 0.0
    # simple: importance + keyword presence
    joined = " ".join([(e.get("title") or "") for e in events]).upper()
    keywords = ["CPI", "PCE", "FOMC", "FED", "POWELL", "NFP", "JOBS", "INFLATION", "RATES", "YIELDS", "TREASURY"]
    hits = sum(1 for k in keywords if k in joined)
    return min(1.0, hits / 10.0)
