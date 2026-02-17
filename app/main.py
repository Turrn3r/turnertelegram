# app/main.py
import io
import gc
import time
import logging
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

import httpx
import pandas as pd

from .config import (
    SYMBOL, LABEL,
    FETCH_CANDLES_EVERY_SECONDS, POST_EVERY_MINUTES, SIGNAL_EVAL_EVERY_SECONDS,
    NEWS_POLL_EVERY_SECONDS, MACRO_POLL_EVERY_SECONDS,
    PRIMARY_INTERVAL, PLOT_WINDOW_MINUTES, ANALYSIS_LOOKBACK_1M, CHART_DPI,
    TRADE_IDEAS_ENABLED, TRADE_MIN_CONF, TRADE_COOLDOWN_SEC, TRADE_NOVELTY_PX,
    NEWS_ENABLED, MACRO_ENABLED, NEWS_MAX_ITEMS, NEWS_RELEVANCE_MIN,
    SENTIMENT_KEYWORDS, MACRO_KEYWORDS,
)
from .twelvedata import fetch_time_series
from .charting import CandleSeries, make_candlestick_png
from .news_sources import fetch_gdelt_gold_news
from .macro_sources import fetch_macro_events
from .signal_engine_gold import build_trade_idea, TradeIdea
from .storage import init_db, upsert_candle, get_last_candles

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("turnertelegram")

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")
scheduler = AsyncIOScheduler()

import os
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID", "") or "").strip()
HAS_TELEGRAM = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

_last_trade_ts = 0.0
_last_trade_dir = None
_last_trade_entry_mid = None

_cached_news: list[dict] = []
_cached_macro: list[dict] = []


def _fmt_price(px: float) -> str:
    return f"{px:,.2f}"


def _fmt_pct(x: float) -> str:
    return f"{x:+.2f}%"


async def _bot() -> Bot:
    return Bot(token=TELEGRAM_BOT_TOKEN)


async def _send_png_document(bot: Bot, filename: str, png: bytes, caption: str) -> None:
    f = io.BytesIO(png)
    f.name = filename
    await bot.send_document(chat_id=TELEGRAM_CHAT_ID, document=f, caption=caption, parse_mode="Markdown")


def _df_from_candles(candles: list[dict]) -> pd.DataFrame:
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


def _sentiment_bias(news_titles: list[str]) -> tuple[str, float]:
    """
    Very lightweight bias model:
    - counts bullish/bearish keyword hits
    Returns (bias, score0to1)
    """
    txt = " ".join(news_titles).lower()
    bull = sum(1 for w in SENTIMENT_KEYWORDS["bullish"] if w in txt)
    bear = sum(1 for w in SENTIMENT_KEYWORDS["bearish"] if w in txt)

    total = bull + bear
    if total == 0:
        return "NEUTRAL", 0.0
    score = min(1.0, total / 8.0)
    if bull > bear:
        return "BULL", score
    if bear > bull:
        return "BEAR", score
    return "NEUTRAL", score


def _macro_bias(events: list[dict]) -> tuple[str, float]:
    """
    Simple macro relevance score (0..1) based on keyword presence.
    Bias remains NEUTRAL by default (true direction depends on surprise/actual-vs-forecast).
    """
    if not events:
        return "NEUTRAL", 0.0
    joined = " ".join((e.get("title","") or "") for e in events).upper()
    hits = sum(1 for k in MACRO_KEYWORDS if k.upper() in joined)
    score = min(1.0, hits / 10.0)
    return "NEUTRAL", score


async def job_fetch_store_1m() -> None:
    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/gold"}) as client:
        candles = await fetch_time_series(client, SYMBOL, interval=PRIMARY_INTERVAL, outputsize=ANALYSIS_LOOKBACK_1M)
        for c in candles:
            upsert_candle(SYMBOL, PRIMARY_INTERVAL, c.t, c.open, c.high, c.low, c.close, c.volume, "twelvedata")


async def job_poll_news() -> None:
    global _cached_news
    if not NEWS_ENABLED:
        return
    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/news"}) as client:
        items = await fetch_gdelt_gold_news(client, max_items=NEWS_MAX_ITEMS)
        _cached_news = [i.__dict__ for i in items if i.relevance >= NEWS_RELEVANCE_MIN]


async def job_poll_macro() -> None:
    global _cached_macro
    if not MACRO_ENABLED:
        return
    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/macro"}) as client:
        ev = await fetch_macro_events(client, max_items=12)
        _cached_macro = [e.__dict__ for e in ev]


async def job_post_chart_and_rundown() -> None:
    if not HAS_TELEGRAM:
        return

    bot = await _bot()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    series = get_last_candles(SYMBOL, PRIMARY_INTERVAL, limit=max(ANALYSIS_LOOKBACK_1M, PLOT_WINDOW_MINUTES + 10))
    if len(series) < PLOT_WINDOW_MINUTES:
        return

    df_all = _df_from_candles(series)
    df_plot = _df_from_candles(series[-PLOT_WINDOW_MINUTES:])
    if df_all.empty or df_plot.empty:
        return

    close = float(df_plot["Close"].iloc[-1])
    prev = float(df_plot["Close"].iloc[-2]) if len(df_plot) >= 2 else close
    ret = (close - prev) / prev * 100.0 if prev else 0.0

    title = f"{LABEL} — 1m candles (last {PLOT_WINDOW_MINUTES} mins)"
    footer = f"Updated {now_utc} • Close {_fmt_price(close)} ({_fmt_pct(ret)})"

    png = make_candlestick_png(CandleSeries(SYMBOL, series[-PLOT_WINDOW_MINUTES:]), title=f"{title}\n{footer}", footer=footer, dpi=CHART_DPI)

    # news summary
    news_titles = [(n.get("title") or "") for n in _cached_news[:5]]
    bias, news_score = _sentiment_bias(news_titles)
    macro_bias, macro_score = _macro_bias(_cached_macro)

    news_lines = ""
    if news_titles:
        news_lines = "\n".join([f"• {t[:110]}" for t in news_titles[:4]])
    else:
        news_lines = "• (no high-relevance news items cached)"

    macro_lines = ""
    if _cached_macro:
        macro_lines = "\n".join([f"• {e.get('country','')} {e.get('title','')}"[:120] for e in _cached_macro[:4]])
    else:
        macro_lines = "• (no macro events cached / key not set)"

    caption = (
        f"*{LABEL}*\n"
        f"• *Close:* `{_fmt_price(close)}` (`{_fmt_pct(ret)}`)\n"
        f"• *Catalyst bias:* `{bias}` | news score `{news_score:.2f}` | macro score `{macro_score:.2f}`\n\n"
        f"*Top news:*\n{news_lines}\n\n"
        f"*Macro:*\n{macro_lines}\n\n"
        "_Lossless chart for zoom clarity._"
    )

    try:
        await _send_png_document(bot, "gold_1m_last15.png", png, caption)
    finally:
        del png
        gc.collect()


async def job_trade_ideas() -> None:
    global _last_trade_ts, _last_trade_dir, _last_trade_entry_mid
    if not (HAS_TELEGRAM and TRADE_IDEAS_ENABLED):
        return

    now_ts = time.time()
    if (now_ts - _last_trade_ts) < TRADE_COOLDOWN_SEC:
        return

    series = get_last_candles(SYMBOL, PRIMARY_INTERVAL, limit=ANALYSIS_LOOKBACK_1M)
    df = _df_from_candles(series)
    if df.empty or len(df) < 240:
        return

    news_titles = [(n.get("title") or "") for n in _cached_news[:6]]
    bias, news_score = _sentiment_bias(news_titles)
    _, macro_score = _macro_bias(_cached_macro)

    catalysts = {
        "bias": bias,
        "news_score": float(news_score),
        "macro_score": float(macro_score),
        "news_titles": news_titles,
    }

    idea: TradeIdea = build_trade_idea(df, catalysts, min_conf=TRADE_MIN_CONF)
    if idea.direction == "NO_TRADE":
        return

    entry_mid = (idea.entry[0] + idea.entry[1]) / 2 if idea.entry else None

    # novelty gating
    if _last_trade_dir == idea.direction and _last_trade_entry_mid and entry_mid:
        if abs(entry_mid - _last_trade_entry_mid) / max(_last_trade_entry_mid, 1e-12) < TRADE_NOVELTY_PX:
            return

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    reasons = "\n".join([f"• {r}" for r in idea.reasons[:10]])
    msg = (
        f"🎯 *Trade Idea (GOLD)* — *{idea.direction}* (confidence `{idea.confidence}/100`)\n"
        f"• *Time:* `{now_utc}`\n"
        f"• *Entry zone:* `{idea.entry[0]:.2f}` → `{idea.entry[1]:.2f}`\n"
        f"• *SL:* `{idea.sl:.2f}`\n"
        f"• *TP1:* `{idea.tp1:.2f}` | *TP2:* `{idea.tp2:.2f}`\n"
        + (f"• *RR (to TP1):* `{idea.rr1:.2f}`\n" if idea.rr1 is not None else "")
        + "\n*Why:*\n"
        f"{reasons}\n\n"
        f"*Catalysts:* bias `{bias}`, news `{news_score:.2f}`, macro `{macro_score:.2f}`\n"
        "_Not financial advice. Use your own risk controls._"
    )

    bot = await _bot()
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")

    _last_trade_ts = now_ts
    _last_trade_dir = idea.direction
    _last_trade_entry_mid = entry_mid


@app.on_event("startup")
async def startup():
    init_db()

    scheduler.add_job(job_fetch_store_1m, "interval", seconds=FETCH_CANDLES_EVERY_SECONDS, max_instances=1, coalesce=True)

    if NEWS_ENABLED:
        scheduler.add_job(job_poll_news, "interval", seconds=NEWS_POLL_EVERY_SECONDS, max_instances=1, coalesce=True)
    if MACRO_ENABLED:
        scheduler.add_job(job_poll_macro, "interval", seconds=MACRO_POLL_EVERY_SECONDS, max_instances=1, coalesce=True)

    scheduler.add_job(job_post_chart_and_rundown, "interval", minutes=POST_EVERY_MINUTES, max_instances=1, coalesce=True)
    scheduler.add_job(job_trade_ideas, "interval", seconds=SIGNAL_EVAL_EVERY_SECONDS, max_instances=1, coalesce=True)

    scheduler.start()
    log.info("Startup complete. GOLD-only. HAS_TELEGRAM=%s", HAS_TELEGRAM)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "ok": True})


@app.get("/health")
async def health():
    return {"ok": True, "telegram": HAS_TELEGRAM, "symbol": LABEL, "interval": PRIMARY_INTERVAL}
