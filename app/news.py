from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import feedparser

# Heavily official / institutional sources (RSS where available)
FEEDS = [
    # US
    ("Federal Reserve Press", "fed", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("US Treasury Press", "treasury", "https://home.treasury.gov/news/press-releases/feed"),
    ("EIA Press", "eia", "https://www.eia.gov/rss/press_rss.xml"),
    ("EIA Gasoline", "eia", "https://www.eia.gov/rss/gasoline.xml"),
    ("CFTC Press", "cftc", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
    ("CFTC Enforcement", "cftc", "https://www.cftc.gov/RSS/RSSENF/rssenf.xml"),
    ("SEC Press", "sec", "https://www.sec.gov/news/pressreleases.rss"),

    # Europe / UK
    ("ECB Press", "ecb", "https://www.ecb.europa.eu/rss/press.html"),
    ("BoE News", "boe", "https://www.bankofengland.co.uk/rss/news"),
    ("BoE Publications", "boe", "https://www.bankofengland.co.uk/rss/publications"),
    ("BIS Speeches", "bis", "https://www.bis.org/list/speeches.rss"),

    # Asia / International
    ("BOJ News", "boj", "https://www.boj.or.jp/en/rss/whatsnew.rdf"),
    ("IMF News", "imf", "https://www.imf.org/en/News/RSS"),
    ("World Bank News", "worldbank", "https://www.worldbank.org/en/news/all/rss"),
    ("IEA News", "iea", "https://www.iea.org/news/rss"),
    ("OPEC Press", "opec", "https://www.opec.org/opec_web/en/press_room/rss.xml"),
]

ASSET_KEYWORDS = {
    # app symbols
    "XRPUSD": [
        "xrp", "ripple", "crypto", "digital asset", "token", "exchange", "stablecoin",
        "sec", "cftc", "regulation", "enforcement", "lawsuit", "etf",
    ],
    "XAUUSD": [
        "gold", "xau", "bullion", "precious metal", "real yields", "inflation",
        "reserve", "central bank", "safe haven", "geopolitical",
    ],
    "XAGUSD": [
        "silver", "xag", "bullion", "precious metal", "industrial demand",
    ],
    "USOIL": [
        "oil", "crude", "wti", "brent", "opec", "iea", "inventory", "eia",
        "refinery", "gasoline", "diesel", "spr", "sanction", "shipping", "pipeline",
    ],
}

POS_WORDS = [
    "approval", "approved", "support", "stimulus", "easing", "rate cut", "cuts rates", "liquidity",
    "inventory draw", "supply cut", "disinflation", "decline", "falls", "lower inflation",
]
NEG_WORDS = [
    "rate hike", "hikes rates", "tightening", "hawkish", "sanction", "sanctions", "tariff", "tariffs",
    "ban", "crackdown", "lawsuit", "sues", "fraud", "probe", "inflation rises", "recession",
    "inventory build", "oversupply",
]

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


def _guid(source: str, title: str, link: str | None) -> str:
    raw = f"{source}|{title}|{link or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _count_hits(text: str, needles: list[str]) -> int:
    t = text.lower()
    return sum(1 for n in needles if n.lower() in t)


def score_text(text: str) -> float:
    # Simple weighted keyword scoring; fast + robust.
    t = text.lower()
    score = 0.0
    score += 1.0 * _count_hits(t, POS_WORDS)
    score -= 1.0 * _count_hits(t, NEG_WORDS)

    # Macro bias boosts: central bank + rates matter broadly
    if "central bank" in t or "interest rate" in t or "inflation" in t:
        score *= 1.15

    return float(score)


def tag_assets(text: str) -> list[str]:
    t = text.lower()
    tags = []
    for asset, kws in ASSET_KEYWORDS.items():
        if any(k.lower() in t for k in kws):
            tags.append(asset)
    return tags


def fetch_news(threshold: float = 2.5, include_neutral: bool = False, max_items: int = 25) -> list[NewsItem]:
    items: list[NewsItem] = []

    for feed_name, source_key, url in FEEDS:
        parsed = feedparser.parse(url)
        for e in parsed.entries[:40]:
            title = _clean(getattr(e, "title", "") or "")
            summary = _clean(getattr(e, "summary", "") or "")
            link = getattr(e, "link", None)
            published = getattr(e, "published", None) or getattr(e, "updated", None)

            if not title:
                continue

            blob = f"{feed_name}\n{source_key}\n{title}\n{summary}"
            tags = tag_assets(blob)
            score = score_text(blob)

            if score >= threshold:
                signal = "BUY"
            elif score <= -threshold:
                signal = "SELL"
            else:
                signal = "NEUTRAL"

            if signal == "NEUTRAL" and not include_neutral:
                continue

            items.append(
                NewsItem(
                    guid=_guid(source_key, title, link),
                    source=source_key,
                    title=title,
                    link=link,
                    summary=summary or None,
                    published=published,
                    tags=",".join(tags) if tags else "macro",
                    score=score,
                    signal=signal,
                )
            )

    # Strongest first
    items.sort(key=lambda x: (abs(x.score), x.published or ""), reverse=True)
    return items[: max(1, min(int(max_items), 100))]
