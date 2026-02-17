from __future__ import annotations

import os
import time
import gc
import logging
from datetime import datetime, timezone

import pandas as pd
import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
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

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID", "") or "").strip()
HAS_TELEGRAM = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

_cached_news: list[dict] = []
_cached_macro: list[dict] = []

_last_trade_ts = 0.0
_last_trade_dir = None
_last_trade_entry_mid = None


def _df_from_candles(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["t"] = pd.to_datetime(df["t"], utc=True, errors="coerce")
    df = df.dropna(subset=["t", "open", "high", "low", "close"]).sort_values("t")
    df = df.set_index("t")
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["Open", "High", "Low", "Close"])


async def job_fetch_candles() -> None:
    pol = HttpPolicy(timeout_sec=25, retries=2, backoff_base_sec=0.6)
    async with client("turnertelegram/gold-candles") as c:
        async def _do():
            return await fetch_time_series(c, SETTINGS.symbol, SETTINGS.interval, SETTINGS.analysis_lookback_1m)
        candles = await with_retries(_do, pol)

    for x in candles:
        upsert_candle(SETTINGS.symbol, SETTINGS.interval, x.t, x.open, x.high, x.low, x.close, x.volume, "twelvedata")

    log.info(f"candles_upserted symbol={SETTINGS.symbol} n={len(candles)}")


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

    series = get_last_candles(SETTINGS.symbol, SETTINGS.interval, limit=max(SETTINGS.analysis_lookback_1m, SETTINGS.plot_window_minutes + 10))
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

    png = make_png(CandleSeries(SETTINGS.symbol, df_plot), title=title, footer=footer, dpi=SETTINGS.chart_dpi)

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
    global _last_trade_ts, _last_trade_dir, _last_trade_entry_mid

    if not HAS_TELEGRAM:
        return

    now_ts = time.time()
    if (now_ts - _last_trade_ts) < SETTINGS.trade_cooldown_sec:
        return

    series = get_last_candles(SETTINGS.symbol, SETTINGS.interval, limit=SETTINGS.analysis_lookback_1m)
    if len(series) < 240:
        return

    df = _df_from_candles(series)
    if df.empty:
        return

    # Assemble features
    feats = assemble_features(df, _cached_news, _cached_macro)

    # Event-risk suppression: if macro is close, skip NEW trades (still can post chart)
    # We can’t reliably parse datetime formats from all feeds; so we use a simple “macro_score high implies risk”
    if SETTINGS.enable_macro and feats["catalysts"]["macro_score"] >= 0.60:
        log.info("trade_suppressed macro_score_high")
        return

    decision = decide(feats, SETTINGS)
    if decision.direction == "NO_TRADE" or decision.risk is None:
        log.info(f"no_trade confidence={decision.confidence}")
        return

    entry_mid = (decision.risk.entry_low + decision.risk.entry_high) / 2.0
    if _last_trade_dir == decision.direction and _last_trade_entry_mid:
        if abs(entry_mid - _last_trade_entry_mid) / max(_last_trade_entry_mid, 1e-12) < SETTINGS.novelty_entry_frac:
            log.info("trade_suppressed novelty_gate")
            return

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

    scheduler.add_job(job_fetch_candles, "interval", seconds=SETTINGS.fetch_candles_every_sec, max_instances=1, coalesce=True)
    if SETTINGS.enable_news:
        scheduler.add_job(job_poll_news, "interval", seconds=SETTINGS.news_poll_every_sec, max_instances=1, coalesce=True)
    if SETTINGS.enable_macro:
        scheduler.add_job(job_poll_macro, "interval", seconds=SETTINGS.macro_poll_every_sec, max_instances=1, coalesce=True)

    scheduler.add_job(job_post_chart, "interval", minutes=SETTINGS.post_chart_every_min, max_instances=1, coalesce=True)
    scheduler.add_job(job_evaluate_and_signal, "interval", seconds=SETTINGS.evaluate_every_sec, max_instances=1, coalesce=True)

    scheduler.start()
    log.info(f"startup ok telegram={HAS_TELEGRAM} symbol={SETTINGS.symbol}")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "ok": True})


@app.get("/health")
async def health():
    return {
        "ok": True,
        "telegram": HAS_TELEGRAM,
        "symbol": SETTINGS.symbol,
        "interval": SETTINGS.interval,
        "news_cached": len(_cached_news),
        "macro_cached": len(_cached_macro),
    }
