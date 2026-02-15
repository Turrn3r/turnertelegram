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

# Simple heuristic lexicons (keep small + explainable)
POS_WORDS = [
    "approval", "approved", "wins", "win", "settles", "settlement", "cuts", "cut", "decline", "falls",
    "lower inflation", "disinflation", "rate cut", "eases", "stimulus", "support", "bullish", "rally"
]
NEG_WORDS = [
    "lawsuit", "sues", "charge", "charged", "fraud", "ban", "sanction", "crackdown", "probe",
    "rate hike", "tighten", "tightening", "hawkish", "higher inflation", "inflation rises",
    "recession", "sell-off", "bearish"
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


def _clean(text: str)_
