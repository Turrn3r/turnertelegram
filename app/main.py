# app/main.py
from __future__ import annotations

import os
import time
import gc
import logging
from datetime import datetime, timezone

import pandas as pd
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

from .config import SETTINGS
from .core.logging import setup_logging
from .core.http import client, HttpPolicy, with_retries

from .datasources.twelvedata import (
    fetch_time_series,
    resolve_gold_symbol,
    InvalidSymbolError,
)
from .datasources.gdelt import fetch_gold_news
from .datasources.tradingeconomics import fetch_calendar

from .features.assemble import assemble_features
from .engine.decision import decide

from .publishing.charting import CandleSeries, make_png
from .publishing.telegram import send_png, render_trade

from .storage import init_db, upsert_candle, get_last_candles

setup_logging()
log = logging.getLogger("goldbot")

# avoid leaking API keys via verbose request logging
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")
scheduler = AsyncIOScheduler()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID", "") or "").strip()
HAS_TELEGRAM = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

# runtime caches
_cached_news: list[dict] = []
_cached_macro: list[dict] = []

# trade gating memory
_last_trade_ts = 0.0
_last_trade_dir: str | None = None
_last_trade_entry_mid: float | None = None

# resolved TwelveData symbol (critical fix)
_RESOLVED_SYMBOL: str | None = None


def _now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _df_from_candles(candles: list[dict]) -> pd.DataFrame:
    """
    Expects list of dicts with keys: t, open, high, low, close, volume(optional)
    """
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles)
    df["t"] = pd.to_datetime(df["t"], utc=True, errors="coerce")
    df = df.dropna(subset=["t", "open", "high", "low", "close"]).sort_values("t")
    df = df.set_index("t")

    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna(subset=["Open", "High", "Low", "Close"])


async def _get_bot() -> Bot:
    return Bot(token=TELEGRAM_BOT_TOKEN)


def _symbol_for_runtime() -> str:
    # Use resolved symbol when available; otherwise prefer first candidate.
    return _RESOLVED_SYMBOL or SETTINGS.symbol_candidates[0]


# =========================
# JOBS
# =========================

async def job_fetch_candles() -> None:
    """
    Fetch 1m candles from TwelveData and store in DB.
    Includes symbol resolver + no-retry on invalid symbol.
    """
    global _RESOLVED_SYMBOL

    pol = HttpPolicy(timeout_sec=25, retries=2, backoff_base_sec=0.6)

    async with client("turnertelegram/gold-candles") as c:
        # Resolve symbol once, cache it
        if _RESOLVED_SYMBOL is None:
            try:
                _RESOLVED_SYMBOL = await resolve_gold_symbol(c, SETTINGS.symbol_candidates)
                log.info(f"twelvedata_symbol_resolved symbol={_RESOLVED_SYMBOL}")
            except Exception as e:
                log.error(f"twelvedata_symbol_resolve_failed err={e}")
                return

        async def _do():
            return await fetch_time_series(
                c,
                _RESOLVED_SYMBOL,
                SETTINGS.interval,
                SETTINGS.analysis_lookback_1m,
            )

        try:
            candles = await with_retries(_do, pol)
        except InvalidSymbolError as e:
            # Don't retry invalid symbol; force re-resolve next run
            log.error(f"invalid_symbol_no_retry symbol={_RESOLVED_SYMBOL} err={e}")
            _RESOLVED_SYMBOL = None
            return
        except Exception as e:
            log.error(f"candle_fetch_failed symbol={_RESOLVED_SYMBOL} err={e}")
            return

    # Store candles
    stored = 0
    for x in candles:
        upsert_candle(
            _RESOLVED_SYMBOL,
            SETTINGS.interval,
            x.t,
            x.open,
            x.high,
            x.low,
            x.close,
            x.volume,
            "twelvedata",
        )
        stored += 1

    log.info(f"candles_upserted symbol={_RESOLVED_SYMBOL} n={stored}")


async def job_poll_news() -> None:
    global _cached_news
    if not SETTINGS.enable_news:
        return

    pol = HttpPolicy(timeout_sec=25, retries=1, backoff_base_sec=0.8)

    async with client("turnertelegram/gold-news") as c:
        async def _do():
            items = await fetch_gold_news(c, SETTINGS.news_max_items)
            # filter by relevance
            return [i.__dict__ for i in items if i.relevance >= SETTINGS.news_relevance_min]

        try:
            _cached_news = await with_retries(_do, pol)
            log.info(f"news_cached n={len(_cached_news)}")
        except Exception as e:
            log.error(f"news_fetch_failed err={e}")


async def job_poll_macro() -> None:
    global _cached_macro
    if not SETTINGS.enable_macro:
        return

    pol = HttpPolicy(timeout_sec=25, retries=1, backoff_base_sec=0.8)

    async with client("turnertelegram/gold-macro") as c:
        async def _do():
            ev = await fetch_calendar(c, max_items=20)
            return [e.__dict__ for e in ev]

        try:
            _cached_macro = await with_retries(_do, pol)
            log.info(f"macro_cached n={len(_cached_macro)}")
        except Exception as e:
            log.error(f"macro_fetch_failed err={e}")


async def job_post_chart() -> None:
    """
    Posts a crisp, lossless chart to Telegram every N minutes.
    """
    if not HAS_TELEGRAM:
        return

    sym = _symbol_for_runtime()

    series = get_last_candles(
        sym,
        SETTINGS.interval,
        limit=max(SETTINGS.analysis_lookback_1m, SETTINGS.plot_window_minutes + 10),
    )
    if len(series) < SETTINGS.plot_window_minutes:
        log.info("chart_skip_not_enough_data")
        return

    df = _df_from_candles(series)
    if df.empty or len(df) < 2:
        log.info("chart_skip_df_empty")
        return

    close = float(df["Close"].iloc[-1])
    prev = float(df["Close"].iloc[-2])
    ret = (close - prev) / prev * 100.0 if prev else 0.0

    now_utc = _now_utc_str()
    title = f"{SETTINGS.label} — 1m candles (last {SETTINGS.plot_window_minutes} mins)"
    footer = f"Updated {now_utc} • Close {close:,.2f} ({ret:+.2f}%) • sym {sym}"

    png = make_png(
        CandleSeries(sym, series[-SETTINGS.plot_window_minutes:]),
        title=title,
        footer=footer,
        dpi=SETTINGS.chart_dpi,
    )

    caption = (
        f"*{SETTINGS.label}*\n"
        f"• *Symbol:* `{sym}`\n"
        f"• *Close:* `{close:,.2f}` (`{ret:+.2f}%`)\n"
        f"• *News cached:* `{len(_cached_news)}` | *Macro cached:* `{len(_cached_macro)}`\n"
        f"• *Time:* `{now_utc}`\n"
        "_Lossless chart for zoom clarity._"
    )

    bot = await _get_bot()
    await send_png(bot, TELEGRAM_CHAT_ID, "gold_1m_last15.png", png, caption)

    del png
    gc.collect()
    log.info("chart_posted")


async def job_evaluate_and_signal() -> None:
    """
    Runs feature assembly + decision engine and posts a trade idea when strong enough.
    """
    global _last_trade_ts, _last_trade_dir, _last_trade_entry_mid

    if not HAS_TELEGRAM:
        return

    now_ts = time.time()
    if (now_ts - _last_trade_ts) < SETTINGS.trade_cooldown_sec:
        return

    sym = _symbol_for_runtime()

    series = get_last_candles(sym, SETTINGS.interval, limit=SETTINGS.analysis_lookback_1m)
    if len(series) < 240:
        log.info("eval_skip_not_enough_data")
        return

    df = _df_from_candles(series)
    if df.empty:
        log.info("eval_skip_df_empty")
        return

    feats = assemble_features(df, _cached_news, _cached_macro)

    # Simple event-risk suppression (conservative)
    if SETTINGS.enable_macro and float(feats["catalysts"]["macro_score"]) >= 0.60:
        log.info("trade_suppressed macro_score_high")
        return

    decision = decide(feats, SETTINGS)
    if decision.direction == "NO_TRADE" or decision.risk is None:
        log.info(f"no_trade confidence={decision.confidence}")
        return

    entry_mid = (decision.risk.entry_low + decision.risk.entry_high) / 2.0

    # novelty gate
    if _last_trade_dir == decision.direction and _last_trade_entry_mid:
        if abs(entry_mid - _last_trade_entry_mid) / max(_last_trade_entry_mid, 1e-12) < SETTINGS.novelty_entry_frac:
            log.info("trade_suppressed novelty_gate")
            return

    bot = await _get_bot()
    msg = render_trade(decision, SETTINGS.label)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")

    _last_trade_ts = now_ts
    _last_trade_dir = decision.direction
    _last_trade_entry_mid = entry_mid

    log.info(f"trade_sent dir={decision.direction} conf={decision.confidence}")


# =========================
# FASTAPI LIFECYCLE
# =========================

@app.on_event("startup")
async def startup() -> None:
    init_db()

    scheduler.add_job(
        job_fetch_candles,
        "interval",
        seconds=SETTINGS.fetch_candles_every_sec,
        max_instances=1,
        coalesce=True,
    )

    if SETTINGS.enable_news:
        scheduler.add_job(
            job_poll_news,
            "interval",
            seconds=SETTINGS.news_poll_every_sec,
            max_instances=1,
            coalesce=True,
        )

    if SETTINGS.enable_macro:
        scheduler.add_job(
            job_poll_macro,
            "interval",
            seconds=SETTINGS.macro_poll_every_sec,
            max_instances=1,
            coalesce=True,
        )

    scheduler.add_job(
        job_post_chart,
        "interval",
        minutes=SETTINGS.post_chart_every_min,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        job_evaluate_and_signal,
        "interval",
        seconds=SETTINGS.evaluate_every_sec,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    log.info(f"startup ok telegram={HAS_TELEGRAM} symbol_candidates={SETTINGS.symbol_candidates}")


# =========================
# ROUTES
# =========================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "ok": True})


@app.get("/health")
async def health():
    return {
        "ok": True,
        "telegram": HAS_TELEGRAM,
        "resolved_symbol": _RESOLVED_SYMBOL,
        "symbol_candidates": list(SETTINGS.symbol_candidates),
        "interval": SETTINGS.interval,
        "news_cached": len(_cached_news),
        "macro_cached": len(_cached_macro),
        "now_utc": _now_utc_str(),
    }


@app.post("/debug/resolve_symbol")
async def debug_resolve_symbol():
    """
    Useful when you want to force a symbol resolve without waiting for the scheduler.
    """
    global _RESOLVED_SYMBOL
    async with client("turnertelegram/debug") as c:
        _RESOLVED_SYMBOL = await resolve_gold_symbol(c, SETTINGS.symbol_candidates)
    return {"resolved_symbol": _RESOLVED_SYMBOL}


@app.post("/debug/run_once")
async def debug_run_once():
    """
    Runs one full cycle (fetch->store->features->decision) and returns the decision JSON.
    Does not send Telegram messages. Great for testing.
    """
    sym = _symbol_for_runtime()

    series = get_last_candles(sym, SETTINGS.interval, limit=SETTINGS.analysis_lookback_1m)
    df = _df_from_candles(series)

    if df.empty:
        return {"ok": False, "error": "No candle data in DB yet. Wait for fetch job or fix symbol."}

    feats = assemble_features(df, _cached_news, _cached_macro)
    decision = decide(feats, SETTINGS)

    out = {
        "ok": True,
        "symbol": sym,
        "decision": {
            "direction": decision.direction,
            "confidence": decision.confidence,
            "reasons": decision.reasons,
            "risk": None if decision.risk is None else {
                "entry_low": decision.risk.entry_low,
                "entry_high": decision.risk.entry_high,
                "sl": decision.risk.sl,
                "tp1": decision.risk.tp1,
                "tp2": decision.risk.tp2,
                "rr": decision.risk.rr,
            },
        },
        "catalysts": feats.get("catalysts"),
        "regime": feats.get("regime"),
        "flow": feats.get("flow"),
    }
    return out
