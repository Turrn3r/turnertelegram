diff --git a/app/news.py b/app/news.py
index b4d713d9f25dfc2ad146291b486842c4eb531423..084c5f24578ca8f4c74931d324602062b231cd80 100644
--- a/app/news.py
+++ b/app/news.py
@@ -1,59 +1,70 @@
 from __future__ import annotations
 
 import hashlib
 import re
 from dataclasses import dataclass
 
 import feedparser
 
 FEEDS = [
+    # US
     ("Fed Press (All)", "fed", "https://www.federalreserve.gov/feeds/press_all.xml"),
     ("Fed Press (Monetary)", "fed", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
-    ("BoE News", "boe", "https://www.bankofengland.co.uk/rss/news"),
-    ("BoE Publications", "boe", "https://www.bankofengland.co.uk/rss/publications"),
+    ("US Treasury Press", "treasury", "https://home.treasury.gov/news/press-releases/feed"),
     ("EIA Press", "eia", "https://www.eia.gov/rss/press_rss.xml"),
     ("EIA Gas/Diesel", "eia", "https://www.eia.gov/rss/gasoline.xml"),
     ("CFTC General PR", "cftc", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
     ("CFTC Enforcement PR", "cftc", "https://www.cftc.gov/RSS/RSSENF/rssenf.xml"),
+    # Europe + UK
+    ("ECB Press", "ecb", "https://www.ecb.europa.eu/rss/press.html"),
+    ("BoE News", "boe", "https://www.bankofengland.co.uk/rss/news"),
+    ("BoE Publications", "boe", "https://www.bankofengland.co.uk/rss/publications"),
+    ("BIS Speeches", "bis", "https://www.bis.org/list/speeches.rss"),
+    # Asia / International institutions
+    ("BOJ News", "boj", "https://www.boj.or.jp/en/rss/whatsnew.rdf"),
+    ("IMF Press", "imf", "https://www.imf.org/en/News/RSS"),
+    ("World Bank News", "worldbank", "https://www.worldbank.org/en/news/all/rss"),
+    ("IEA News", "iea", "https://www.iea.org/news/rss"),
+    ("OPEC Press", "opec", "https://www.opec.org/opec_web/en/press_room/rss.xml"),
 ]
 
 ASSET_TAGS = {
-    "XRPUSD": ["xrp", "ripple", "sec", "etf", "crypto", "stablecoin", "digital asset"],
-    "XAUUSD": ["gold", "xau", "bullion", "precious metal", "inflation", "real yields", "safe haven"],
+    "XRPUSD": ["xrp", "ripple", "sec", "etf", "crypto", "stablecoin", "digital asset", "tokenization", "exchange"],
+    "XAUUSD": ["gold", "xau", "bullion", "precious metal", "inflation", "real yields", "safe haven", "central bank reserves"],
     "XAGUSD": ["silver", "xag", "bullion", "precious metal"],
-    "CL.F":   ["oil", "wti", "brent", "crude", "opec", "inventory", "refinery", "gasoline", "diesel", "spr"],
+    "CL.F":   ["oil", "wti", "brent", "crude", "opec", "inventory", "refinery", "gasoline", "diesel", "spr", "sanction", "shipping"],
 }
 
 POS_WORDS = [
     "approval", "approved", "wins", "win", "settles", "settlement",
     "cuts", "cut", "decline", "falls",
     "disinflation", "rate cut", "eases", "stimulus", "support", "bullish", "rally",
 ]
 NEG_WORDS = [
     "lawsuit", "sues", "charge", "charged", "fraud", "ban", "sanction", "crackdown", "probe",
-    "rate hike", "tighten", "tightening", "hawkish",
+    "rate hike", "tighten", "tightening", "hawkish", "sanctions", "tariffs",
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
