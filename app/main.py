# app/main.py
from __future__ import annotations

import os
import time
import gc
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

from .config import SETTINGS
from .core.logging import setup_logging
from .core.http import client, HttpPolicy, with_retries

from .datasources.twelvedata import fetch_time_series
from .datasources.gdelt import fetch_gold_news
from .datasources.tradingeconomics import fetch_calendar

from .features.assemble import assemble_features
from .engine.decision import decide

from .publishing.charting import CandleSeries, make_png
from .publishing.telegram import send_png, render_trade

from .storage import init_db, upsert_candle, get_last_candles

setup_logging()
log = logging.getLogger("goldbot")

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")
scheduler = AsyncIOScheduler()

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID", "") or "").strip()
HAS_TELEGRAM = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

# --- CORS for your site + php proxy (if used) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[SETTINGS.allowed_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_cached_news: list[dict] = []
_cached_macro: list[dict] = []

_last_trade_ts = 0.0
_last_trade_dir: str | None = None
_last_trade_entry_mid: float | None = None

# ✅ Store latest computed decision so the website can fetch it
_latest_signal: dict[str, Any] | None = None

# ✅ Resolved TwelveData symbol (prevents your missing/invalid issue)
_RESOLVED_SYMBOL: str | None = None


def _df_from_candles(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["t"] = pd.to_datetime(df["t"], utc=True, errors="coerce")
    df = df.dropna(subset=["t", "open", "high", "low", "close"]).sort_values("t")
    df = df.set_index("t")
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["Open", "High", "Low", "Close"])


async def _resolve_twelvedata_symbol() -> str:
    """
    Try multiple candidate symbols (plan/market dependent). Cache the first that works.
    """
    global _RESOLVED_SYMBOL
    if _RESOLVED_SYMBOL:
        return _RESOLVED_SYMBOL

    pol = HttpPolicy(timeout_sec=20, retries=1, backoff_base_sec=0.4)
    candidates = list(SETTINGS.symbol_candidates) or [SETTINGS.symbol]

    async with client("turnertelegram/td-symbol-probe") as c:

        async def _probe(sym: str) -> bool:
            try:
                # minimal request; if symbol invalid, TwelveData returns message and fetch_time_series raises
                _ = await fetch_time_series(c, sym, interval="1min", outputsize=2)
                return True
            except Exception:
                return False

        for sym in candidates:
            ok = await with_retries(lambda s=sym: _probe(s), pol)
            if ok:
                _RESOLVED_SYMBOL = sym
                log.info(f"twelvedata_symbol_resolved symbol={sym}")
                return sym

    # If nothing works, keep original so errors remain visible
    _RESOLVED_SYMBOL = SETTINGS.symbol
    log.warning(f"twelvedata_symbol_fallback symbol={_RESOLVED_SYMBOL}")
    return _RESOLVED_SYMBOL


async def job_fetch_candles() -> None:
    symbol = await _resolve_twelvedata_symbol()

    pol = HttpPolicy(timeout_sec=25, retries=2, backoff_base_sec=0.6)
    async with client("turnertelegram/gold-candles") as c:

        async def _do():
            return await fetch_time_series(c, symbol, SETTINGS.interval, SETTINGS.analysis_lookback_1m)

        candles = await with_retries(_do, pol)

    for x in candles:
        upsert_candle(symbol, SETTINGS.interval, x.t, x.open, x.high, x.low, x.close, x.volume, "twelvedata")

    log.info(f"candles_upserted symbol={symbol} n={len(candles)}")


async def job_poll_news() -> None:
    global _cached_news
    if not SETTINGS.enable_news:
        return

    pol = HttpPolicy(timeout_sec=25, retries=1, backoff_base_sec=0.8)
    async with client("turnertelegram/gold-news") as c:

        async def _do():
            items = await fetch_gold_news(c, SETTINGS.news_max_items)
            return [i.__dict__ for i in items if i.relevance >= SETTINGS.news_relevance_min]

        _cached_news = await with_retries(_do, pol)

    log.info(f"news_cached n={len(_cached_news)}")


async def job_poll_macro() -> None:
    global _cached_macro
    if not SETTINGS.enable_macro:
        return

    pol = HttpPolicy(timeout_sec=25, retries=1, backoff_base_sec=0.8)
    async with client("turnertelegram/gold-macro") as c:

        async def _do():
            ev = await fetch_calendar(c, max_items=20)
            return [e.__dict__ for e in ev]

        _cached_macro = await with_retries(_do, pol)

    log.info(f"macro_cached n={len(_cached_macro)}")


async def job_post_chart() -> None:
    if not HAS_TELEGRAM:
        return

    symbol = await _resolve_twelvedata_symbol()

    # ✅ keep the chart strictly “last 15 minutes”
    series = get_last_candles(symbol, SETTINGS.interval, limit=max(SETTINGS.plot_window_minutes + 5, 30))
    if len(series) < SETTINGS.plot_window_minutes:
        return

    df = _df_from_candles(series)
    df_plot = series[-SETTINGS.plot_window_minutes:]
    if df.empty:
        return

    close = float(df["Close"].iloc[-1])
    prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else close
    ret = (close - prev) / prev * 100.0 if prev else 0.0

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = f"{SETTINGS.label} — 1m candles (last {SETTINGS.plot_window_minutes} mins)"
    footer = f"Updated {now_utc} • Close {close:,.2f} ({ret:+.2f}%)"

    png = make_png(CandleSeries(symbol, df_plot), title=title, footer=footer, dpi=SETTINGS.chart_dpi)

    cap = (
        f"*{SETTINGS.label}*\n"
        f"• *Close:* `{close:,.2f}` (`{ret:+.2f}%`)\n"
        f"• *News cached:* `{len(_cached_news)}` | *Macro cached:* `{len(_cached_macro)}`\n"
        f"• *Time:* `{now_utc}`\n"
        "_Lossless chart for zoom clarity._"
    )

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await send_png(bot, TELEGRAM_CHAT_ID, "gold_1m_last15.png", png, cap)
    del png
    gc.collect()


async def job_evaluate_and_signal() -> None:
    global _last_trade_ts, _last_trade_dir, _last_trade_entry_mid, _latest_signal

    symbol = await _resolve_twelvedata_symbol()

    # still compute signal even if telegram disabled (website uses it)
    now_ts = time.time()
    if HAS_TELEGRAM and (now_ts - _last_trade_ts) < SETTINGS.trade_cooldown_sec:
        return

    series = get_last_candles(symbol, SETTINGS.interval, limit=SETTINGS.analysis_lookback_1m)
    if len(series) < 240:
        return

    df = _df_from_candles(series)
    if df.empty:
        return

    feats = assemble_features(df, _cached_news, _cached_macro)

    # macro-risk suppression
    if SETTINGS.enable_macro and feats["catalysts"]["macro_score"] >= 0.60:
        log.info("trade_suppressed macro_score_high")
        _latest_signal = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "direction": "NO_TRADE",
            "reason": "macro_risk_high",
            "features": feats,
        }
        return

    decision = decide(feats, SETTINGS)

    # ✅ always publish latest signal to API
    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "interval": SETTINGS.interval,
        "direction": decision.direction,
        "confidence": getattr(decision, "confidence", None),
        "risk": getattr(decision, "risk", None).__dict__ if getattr(decision, "risk", None) else None,
        "rundown": getattr(decision, "rundown", None),
        "features": feats,
        "news_cached": _cached_news[:12],
        "macro_cached": _cached_macro[:20],
    }
    _latest_signal = payload

    if decision.direction == "NO_TRADE" or decision.risk is None:
        log.info(f"no_trade confidence={decision.confidence}")
        return

    entry_mid = (decision.risk.entry_low + decision.risk.entry_high) / 2.0
    if _last_trade_dir == decision.direction and _last_trade_entry_mid:
        if abs(entry_mid - _last_trade_entry_mid) / max(_last_trade_entry_mid, 1e-12) < SETTINGS.novelty_entry_frac:
            log.info("trade_suppressed novelty_gate")
            return

    if HAS_TELEGRAM:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        msg = render_trade(decision, SETTINGS.label)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")

        _last_trade_ts = now_ts
        _last_trade_dir = decision.direction
        _last_trade_entry_mid = entry_mid

        log.info(f"trade_sent dir={decision.direction} conf={decision.confidence}")


@app.on_event("startup")
async def startup():
    init_db()

    # resolve symbol once at startup to avoid “missing/invalid”
    await _resolve_twelvedata_symbol()

    scheduler.add_job(job_fetch_candles, "interval", seconds=SETTINGS.fetch_candles_every_sec, max_instances=1, coalesce=True)
    if SETTINGS.enable_news:
        scheduler.add_job(job_poll_news, "interval", seconds=SETTINGS.news_poll_every_sec, max_instances=1, coalesce=True)
    if SETTINGS.enable_macro:
        scheduler.add_job(job_poll_macro, "interval", seconds=SETTINGS.macro_poll_every_sec, max_instances=1, coalesce=True)

    scheduler.add_job(job_post_chart, "interval", minutes=SETTINGS.post_chart_every_min, max_instances=1, coalesce=True)
    scheduler.add_job(job_evaluate_and_signal, "interval", seconds=SETTINGS.evaluate_every_sec, max_instances=1, coalesce=True)

    scheduler.start()
    log.info(f"startup ok telegram={HAS_TELEGRAM} symbol={_RESOLVED_SYMBOL} interval={SETTINGS.interval}")


# ---------- UI ----------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "ok": True})


@app.get("/trade", response_class=HTMLResponse)
async def trade_ui(request: Request):
    return templates.TemplateResponse("trade.html", {"request": request})


# ---------- APIs ----------
@app.get("/health")
async def health():
    sym = _RESOLVED_SYMBOL or SETTINGS.symbol
    return {
        "ok": True,
        "telegram": HAS_TELEGRAM,
        "symbol": sym,
        "interval": SETTINGS.interval,
        "news_cached": len(_cached_news),
        "macro_cached": len(_cached_macro),
        "has_latest_signal": _latest_signal is not None,
    }


@app.get("/api/signal/latest")
async def api_latest_signal():
    if not _latest_signal:
        return JSONResponse({"ok": False, "error": "no_signal_yet"}, status_code=404)
    return {"ok": True, "signal": _latest_signal}
