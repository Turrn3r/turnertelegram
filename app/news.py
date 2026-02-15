from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser

# --- Official / public RSS feeds ---
FEEDS = [
    # Federal Reserve (press releases)
    ("Fed Press (All)", "fed", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("Fed Press (Monetary)", "fed", "https://www.federalreserve.gov/feeds/press_monetary.xml"),

    # Bank of England
    ("BoE News", "boe", "https://www.bankofengland.co.uk/rss/news"),
    ("BoE Publications", "boe", "https://www.bankofengland.co.uk/rss/publications"),

    # EIA (energy) – useful for Oil
    ("EIA Press", "eia", "https://www.eia.gov/rss/press_rss.xml"),
    ("EIA Gas/Diesel", "eia", "https://www.eia.gov/rss/gasoline.xml"),

    # CFTC (regulatory / enforcement)
    ("CFTC General PR", "cftc", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
    ("CFTC Enforcement PR", "cftc", "https://www.cftc.gov/RSS/RSSENF/rssenf.xml"),
]

# Map sources/keywords to assets
ASSET_TAGS = {
    "XRPUSD": ["xrp", "ripple", "sec", "etf", "crypto", "stablecoin", "digital asset"],
    "XAUUSD": ["gold", "xau", "bullion", "precious metal", "inflation", "real yields", "safe haven"],
    "XAGUSD": ["silver", "xag", "bullion", "precious metal"],
    "CL.F":   ["oil", "wti", "brent", "crude", "opec", "inventory", "refinery", "gasoline", "diesel", "spr"],
}

# Simple heuristic lexicons
POS_WORDS = [
    "approval", "approved", "wins", "win", "settles", "settlement",
    "cuts", "cut", "decline", "falls",
    "disinflation", "rate cut", "eases", "stimulus", "support", "bullish", "rally",
]
NEG_WORDS = [
    "lawsuit", "sues", "charge", "charged", "fraud", "ban", "sanction", "crackdown", "probe",
    "rate hike", "tighten", "tightening", "hawkish",
    "inflation rises", "recession", "sell-off", "bearish",
]

# Commodity-specific hooks
OIL_BULL = ["inventory draw", "inventories fall", "supply cut", "opec cut", "disruption", "pipeline outage"]
OIL_BEAR = ["inventory build", "inventories rise", "demand weakness", "oversupply", "production increase"]

METALS_BULL = ["safe haven", "risk-off", "geopolitical", "uncertainty", "rate cut", "yields fall"]
METALS_BEAR = ["yields rise", "hawkish", "rate hike", "strong dollar"]


@dataclass
class NewsItem:
    guid: str
    source: str
    title: str
    link: str | None
    summary: str | None
    published: str | None
    tags: str
    score: float
    signal: str  # BUY / SELL / NEUTRAL


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _hash_guid(*parts: str) -> str:
    payload = "||".join([p or "" for p in parts])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _score(text: str) -> float:
    t = text.lower()
    score = 0.0

    for w in POS_WORDS:
        if w in t:
            score += 1.0
    for w in NEG_WORDS:
        if w in t:
            score -= 1.0

    # Extra commodity nuance
    if any(k in t for k in OIL_BULL):
        score += 1.5
    if any(k in t for k in OIL_BEAR):
        score -= 1.5

    if any(k in t for k in METALS_BULL):
        score += 1.0
    if any(k in t for k in METALS_BEAR):
        score -= 1.0

    return score


def _detect_tags(text: str) -> str:
    t = text.lower()
    tags = []
    for sym, keys in ASSET_TAGS.items():
        if any(k in t for k in keys):
            tags.append(sym)
    return ",".join(tags) if tags else "MACRO"


def _signal_from_score(score: float, threshold: float) -> str:
    if score >= threshold:
        return "BUY"
    if score <= -threshold:
        return "SELL"
    return "NEUTRAL"


def fetch_news(threshold: float = 2.0) -> list[NewsItem]:
    now = datetime.now(timezone.utc)
    out: list[NewsItem] = []

    for (name, source, url) in FEEDS:
        d = feedparser.parse(url)

        for e in d.entries[:20]:
            title = _clean(getattr(e, "title", ""))
            link = _clean(getattr(e, "link", "")) or None
            summary = _clean(getattr(e, "summary", "")) or None
            published = _clean(getattr(e, "published", "")) or None

            guid = _hash_guid(source, title, link or "", published or "")

            combined = f"{title} {summary or ''}".strip()
            tags = _detect_tags(combined)
            sc = _score(combined)
            sig = _signal_from_score(sc, threshold)

            out.append(
                NewsItem(
                    guid=guid,
                    source=f"{source}:{name}",
                    title=title or "(no title)",
                    link=link,
                    summary=summary,
                    published=published,
                    tags=tags,
                    score=sc,
                    signal=sig,
                )
            )

    return out
