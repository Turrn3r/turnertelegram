from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import feedparser

FEEDS = [
    # US
    ("Fed Press (All)", "fed", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("Fed Press (Monetary)", "fed", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("US Treasury Press", "treasury", "https://home.treasury.gov/news/press-releases/feed"),
    ("EIA Press", "eia", "https://www.eia.gov/rss/press_rss.xml"),
    ("EIA Gas/Diesel", "eia", "https://www.eia.gov/rss/gasoline.xml"),
    ("CFTC General PR", "cftc", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
    ("CFTC Enforcement PR", "cftc", "https://www.cftc.gov/RSS/RSSENF/rssenf.xml"),
    # Europe + UK
    ("ECB Press", "ecb", "https://www.ecb.europa.eu/rss/press.html"),
    ("BoE News", "boe", "https://www.bankofengland.co.uk/rss/news"),
    ("BoE Publications", "boe", "https://www.bankofengland.co.uk/rss/publications"),
    ("BIS Speeches", "bis", "https://www.bis.org/list/speeches.rss"),
    # Asia / International institutions
    ("BOJ News", "boj", "https://www.boj.or.jp/en/rss/whatsnew.rdf"),
    ("IMF Press", "imf", "https://www.imf.org/en/News/RSS"),
    ("World Bank News", "worldbank", "https://www.worldbank.org/en/news/all/rss"),
    ("IEA News", "iea", "https://www.iea.org/news/rss"),
    ("OPEC Press", "opec", "https://www.opec.org/opec_web/en/press_room/rss.xml"),
]

ASSET_TAGS = {
    "XRPUSD": ["xrp", "ripple", "sec", "etf", "crypto", "stablecoin", "digital asset", "tokenization", "exchange"],
    "XAUUSD": ["gold", "xau", "bullion", "precious metal", "inflation", "real yields", "safe haven", "central bank reserves"],
    "XAGUSD": ["silver", "xag", "bullion", "precious metal"],
    "CL.F":   ["oil", "wti", "brent", "crude", "opec", "inventory", "refinery", "gasoline", "diesel", "spr", "sanction", "shipping"],
}

POS_WORDS = [
    "approval", "approved", "wins", "win", "settles", "settlement",
    "cuts", "cut", "decline", "falls",
    "disinflation", "rate cut", "eases", "stimulus", "support", "bullish", "rally",
]
NEG_WORDS = [
    "lawsuit", "sues", "charge", "charged", "fraud", "ban", "sanction", "crackdown", "probe",
    "rate hike", "tighten", "tightening", "hawkish", "sanctions", "tariffs",
    "inflation rises", "recession", "sell-off", "bearish",
]

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


def _mk_guid(source: str, title: str, link: str | None) -> str:
    raw = f"{source}|{title}|{link or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _contains_any(hay: str, needles: list[str]) -> bool:
    h = hay.lower()
    return any(n.lower() in h for n in needles)


def _count_hits(hay: str, needles: list[str]) -> int:
    h = hay.lower()
    return sum(1 for n in needles if n.lower() in h)


def _score_text(text: str) -> float:
    t = text.lower()
    score = 0.0

    score += 1.0 * _count_hits(t, POS_WORDS)
    score -= 1.0 * _count_hits(t, NEG_WORDS)

    # domain nudges
    score += 0.75 * _count_hits(t, OIL_BULL)
    score -= 0.75 * _count_hits(t, OIL_BEAR)

    score += 0.5 * _count_hits(t, METALS_BULL)
    score -= 0.5 * _count_hits(t, METALS_BEAR)

    return score


def fetch_news(threshold: float = 2.5, include_neutral: bool = False) -> list[NewsItem]:
    items: list[NewsItem] = []

    for feed_name, source_key, url in FEEDS:
        parsed = feedparser.parse(url)
        for e in parsed.entries[:30]:
            title = _clean(getattr(e, "title", "") or "")
            summary = _clean(getattr(e, "summary", "") or "")
            link = getattr(e, "link", None)
            published = getattr(e, "published", None) or getattr(e, "updated", None)

            if not title:
                continue

            blob = f"{title}\n{summary}\n{feed_name} {source_key}"
            score = _score_text(blob)

            # Tagging by asset keyword matches
            matched_assets = []
            for asset, kws in ASSET_TAGS.items():
                if _contains_any(blob, kws):
                    matched_assets.append(asset)

            tags = ",".join(matched_assets) if matched_assets else "macro"

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
                    guid=_mk_guid(source_key, title, link),
                    source=source_key,
                    title=title,
                    link=link,
                    summary=summary or None,
                    published=published,
                    tags=tags,
                    score=float(score),
                    signal=signal,
                )
            )

    # sort by absolute “strength” first, then newest-ish if available
    items.sort(key=lambda x: (abs(x.score), x.published or ""), reverse=True)
    return items[:25]
