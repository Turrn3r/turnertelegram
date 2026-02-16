from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
import feedparser

BASE_FEEDS = [
    ("Federal Reserve", "fed", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("US Treasury", "treasury", "https://home.treasury.gov/news/press-releases/feed"),
    ("EIA Press", "eia", "https://www.eia.gov/rss/press_rss.xml"),
    ("EIA Gasoline", "eia", "https://www.eia.gov/rss/gasoline.xml"),
    ("CFTC Press", "cftc", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
    ("CFTC Enforcement", "cftc", "https://www.cftc.gov/RSS/RSSENF/rssenf.xml"),
    ("SEC Press", "sec", "https://www.sec.gov/news/pressreleases.rss"),
    ("ECB Press", "ecb", "https://www.ecb.europa.eu/rss/press.html"),
    ("BoE News", "boe", "https://www.bankofengland.co.uk/rss/news"),
    ("BoE Publications", "boe", "https://www.bankofengland.co.uk/rss/publications"),
    ("BIS Speeches", "bis", "https://www.bis.org/list/speeches.rss"),
    ("BOJ Updates", "boj", "https://www.boj.or.jp/en/rss/whatsnew.rdf"),
    ("IMF News", "imf", "https://www.imf.org/en/News/RSS"),
    ("World Bank", "worldbank", "https://www.worldbank.org/en/news/all/rss"),
    ("IEA News", "iea", "https://www.iea.org/news/rss"),
    ("OPEC Press", "opec", "https://www.opec.org/opec_web/en/press_room/rss.xml"),
]

def _parse_extra_feeds() -> list[tuple[str, str, str]]:
    raw = (os.getenv("EXTRA_RSS_FEEDS", "") or "").strip()
    if not raw:
        return []
    out: list[tuple[str, str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "|" in part:
            src, url = part.split("|", 1)
            src = src.strip()[:40] or "extra"
            url = url.strip()
            if url.startswith("http"):
                out.append((f"Extra:{src}", src, url))
    return out

FEEDS = BASE_FEEDS + _parse_extra_feeds()

SYM_XRP = "XRPUSD"
SYM_GOLD = "XAUUSD"
SYM_SILVER = "XAGUSD"
SYM_OIL = "USOIL"

ASSET_KEYWORDS = {
    SYM_XRP: ["xrp", "ripple", "crypto", "digital asset", "token", "exchange", "stablecoin", "sec", "cftc", "etf", "enforcement", "regulation"],
    SYM_GOLD: ["gold", "xau", "bullion", "precious metal", "inflation", "yields", "central bank", "reserve", "safe haven", "geopolitical"],
    SYM_SILVER: ["silver", "xag", "bullion", "precious metal", "industrial", "manufacturing", "solar"],
    SYM_OIL: ["oil", "crude", "wti", "brent", "opec", "iea", "inventory", "eia", "refinery", "spr", "pipeline", "outage", "export", "shipping"],
}

MACRO_TRIGGERS = ["interest rate", "policy", "inflation", "cpi", "ppi", "employment", "jobs", "gdp", "liquidity", "sanction", "tariff", "war", "geopolitical"]

POS = ["rate cut", "easing", "stimulus", "support", "liquidity injection", "inventory draw", "supply cut", "approved", "approval", "ceasefire"]
NEG = ["rate hike", "tightening", "hawkish", "inflation rises", "recession", "ban", "crackdown", "lawsuit", "probe", "fraud", "inventory build", "oversupply", "sanctions", "tariffs"]

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
    signal: str

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def _guid(source: str, title: str, link: str | None) -> str:
    raw = f"{source}|{title}|{link or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _hits(text: str, words: list[str]) -> int:
    t = text.lower()
    return sum(1 for w in words if w.lower() in t)

def tag_assets(text: str) -> list[str]:
    t = text.lower()
    tags: list[str] = []
    for asset, kws in ASSET_KEYWORDS.items():
        if any(k.lower() in t for k in kws):
            tags.append(asset)
    if not tags and any(k in t for k in MACRO_TRIGGERS):
        tags.append("macro")
    if not tags:
        tags.append("macro")
    return tags

def score(text: str, tags: list[str]) -> float:
    t = text.lower()
    s = 0.0
    s += 1.0 * _hits(t, POS)
    s -= 1.0 * _hits(t, NEG)
    macro_intensity = _hits(t, MACRO_TRIGGERS)
    if macro_intensity >= 2:
        s *= 1.20
    elif macro_intensity == 1:
        s *= 1.10
    return float(s)

def fetch_news(threshold: float = 2.5, include_neutral: bool = False, max_items: int = 25) -> list[NewsItem]:
    items: list[NewsItem] = []
    for feed_name, source_key, url in FEEDS:
        parsed = feedparser.parse(url)
        for e in (parsed.entries or [])[:60]:
            title = _clean(getattr(e, "title", "") or "")
            summary = _clean(getattr(e, "summary", "") or "")
            link = getattr(e, "link", None)
            published = getattr(e, "published", None) or getattr(e, "updated", None)
            if not title:
                continue

            blob = f"{feed_name}\n{source_key}\n{title}\n{summary}"
            tags = tag_assets(blob)
            sc = score(blob, tags)

            if sc >= threshold:
                signal = "BUY"
            elif sc <= -threshold:
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
                    tags=",".join(tags),
                    score=sc,
                    signal=signal,
                )
            )
    items.sort(key=lambda x: (abs(x.score), x.published or ""), reverse=True)
    return items[: max(1, min(int(max_items), 200))]
