from __future__ import annotations

from dataclasses import dataclass

# =========================
# GOLD INTELLIGENCE CONFIG
# =========================

@dataclass(frozen=True)
class Settings:
    # Instrument
    symbol: str = "XAUUSD"
    label: str = "Gold (XAU) / USD"

    # Candle config
    interval: str = "1min"
    plot_window_minutes: int = 15
    analysis_lookback_1m: int = 720  # 12h

    # Scheduling
    fetch_candles_every_sec: int = 60
    news_poll_every_sec: int = 180
    macro_poll_every_sec: int = 300
    evaluate_every_sec: int = 60
    post_chart_every_min: int = 15

    # Signal gating
    min_confidence: int = 84
    trade_cooldown_sec: int = 1800       # 30 min
    novelty_entry_frac: float = 0.0012   # 0.12%

    # Risk model
    sl_atr_mult: float = 1.10
    tp1_r: float = 1.6
    tp2_r: float = 2.7

    # News/Macro usage
    enable_news: bool = True
    enable_macro: bool = True
    news_max_items: int = 12
    news_relevance_min: float = 0.20

    # Event-risk suppression: do not open new trades when a high impact event is close
    suppress_new_trades_if_event_within_min: int = 35

    # Chart rendering
    chart_dpi: int = 260


SETTINGS = Settings()
