# app/config.py

# ----------------
# SYSTEM FOCUS
# ----------------
SYMBOL = "XAUUSD"
LABEL = "Gold (XAU) / USD"

# ----------------
# SCHEDULING
# ----------------
FETCH_CANDLES_EVERY_SECONDS = 60
POST_EVERY_MINUTES = 15
SIGNAL_EVAL_EVERY_SECONDS = 60
NEWS_POLL_EVERY_SECONDS = 180  # 3 min
MACRO_POLL_EVERY_SECONDS = 300 # 5 min

# ----------------
# CANDLES
# ----------------
PRIMARY_INTERVAL = "1min"
PLOT_WINDOW_MINUTES = 15
ANALYSIS_LOOKBACK_1M = 720     # 12h history
CHART_DPI = 260

# ----------------
# TRADE IDEAS
# ----------------
TRADE_IDEAS_ENABLED = True
TRADE_MIN_CONF = 80
TRADE_COOLDOWN_SEC = 1800      # 30 min
TRADE_NOVELTY_PX = 0.0012      # 0.12% move in entry mid required to resend same direction

# ----------------
# NEWS / MACRO
# ----------------
NEWS_ENABLED = True
MACRO_ENABLED = True

# news filtering
NEWS_MAX_ITEMS = 8
NEWS_RELEVANCE_MIN = 0.20  # 0..1

# trade risk model
DEFAULT_RISK_R = 1.0   # risk unit
TP1_R = 1.5
TP2_R = 2.5

# sentiment weights (simple but effective)
SENTIMENT_KEYWORDS = {
    "bullish": ["safe haven", "risk-off", "geopolitical", "conflict", "sanctions", "war", "crisis", "inflation"],
    "bearish": ["hawkish", "rate hike", "yields rise", "strong dollar", "dollar strength", "tightening"],
}

# Macro impact keywords
MACRO_KEYWORDS = ["CPI", "PCE", "FOMC", "Fed", "Powell", "NFP", "jobs", "inflation", "rates", "Treasury", "yields"]
