# app/config.py
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    v = (os.getenv(name, "").strip().lower())
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except Exception:
        return default


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name, "").strip()
    return v if v else default


@dataclass(frozen=True)
class Settings:
    # Candles
    label: str = _env_str("LABEL", "XAUUSD")
    interval: str = _env_str("CANDLE_INTERVAL", "1min")
    analysis_lookback_1m: int = _env_int("ANALYSIS_LOOKBACK_1M", 720)  # 12h of 1m
    plot_window_minutes: int = _env_int("PLOT_WINDOW_MINUTES", 15)

    # ✅ FIX: define symbol + candidates (TwelveData accepts different forms on some plans)
    symbol: str = _env_str("SYMBOL", "XAUUSD")
    symbol_candidates: tuple[str, ...] = tuple(
        s.strip() for s in _env_str("SYMBOL_CANDIDATES", "XAUUSD,XAU/USD").split(",") if s.strip()
    )

    fetch_candles_every_sec: int = _env_int("FETCH_CANDLES_EVERY_SEC", 60)

    # News / Macro
    enable_news: bool = _env_bool("ENABLE_NEWS", True)
    enable_macro: bool = _env_bool("ENABLE_MACRO", True)

    news_poll_every_sec: int = _env_int("NEWS_POLL_EVERY_SEC", 180)
    macro_poll_every_sec: int = _env_int("MACRO_POLL_EVERY_SEC", 300)

    news_max_items: int = _env_int("NEWS_MAX_ITEMS", 12)
    news_relevance_min: float = _env_float("NEWS_RELEVANCE_MIN", 0.25)

    # Posting / evaluation
    post_chart_every_min: int = _env_int("POST_CHART_EVERY_MIN", 1)
    evaluate_every_sec: int = _env_int("EVALUATE_EVERY_SEC", 60)

    # Signal throttles
    trade_cooldown_sec: int = _env_int("TRADE_COOLDOWN_SEC", 180)
    novelty_entry_frac: float = _env_float("NOVELTY_ENTRY_FRAC", 0.0015)

    # Chart tuning
    chart_dpi: int = _env_int("CHART_DPI", 240)

    # UI / CORS
    allowed_origin: str = _env_str("ALLOWED_ORIGIN", "https://turrner.com")


SETTINGS = Settings()
