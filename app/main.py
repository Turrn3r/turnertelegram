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

from .config import *
from .twelvedata import fetch_time_series
from .charting import CandleSeries, make_candlestick_png
from .news_sources import fetch_gdelt_gold_news
from .macro_sources import fetch_macro_events
from .signal_engine_gold import build_trade_idea
from .storage import init_db, upsert_candle, get_last_candles

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gold-system")

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")
scheduler = AsyncIOScheduler()

import os
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
HAS_TELEGRAM = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

_last_trade_ts = 0
_last_trade_dir = None
_last_trade_mid = None

_cached_news = []
_cached_macro = []


def df_from_candles(c):
    df = pd.DataFrame(c)
    df["t"] = pd.to_datetime(df["t"])
    df = df.set_index("t")
    df = df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close"})
    return df


def sentiment_bias(news):
    text = " ".join(news).lower()
    bull = sum(1 for w in ["safe haven","geopolitical","war","inflation"] if w in text)
    bear = sum(1 for w in ["rate hike","strong dollar","hawkish"] if w in text)
    if bull > bear:
        return "BULL", min(1.0, bull/5)
    if bear > bull:
        return "BEAR", min(1.0, bear/5)
    return "NEUTRAL", 0


async def job_fetch():
    async with httpx.AsyncClient() as client:
        candles = await fetch_time_series(client, SYMBOL, interval=PRIMARY_INTERVAL, outputsize=ANALYSIS_LOOKBACK_1M)
        for c in candles:
            upsert_candle(SYMBOL, PRIMARY_INTERVAL, c.t, c.open, c.high, c.low, c.close, c.volume, "twelvedata")


async def job_news():
    global _cached_news
    async with httpx.AsyncClient() as client:
        items = await fetch_gdelt_gold_news(client, NEWS_MAX_ITEMS)
        _cached_news = [i.title for i in items]


async def job_macro():
    global _cached_macro
    async with httpx.AsyncClient() as client:
        events = await fetch_macro_events(client)
        _cached_macro = [e.title for e in events]


async def job_signal():
    global _last_trade_ts, _last_trade_dir, _last_trade_mid

    if not HAS_TELEGRAM:
        return

    now = time.time()
    if now - _last_trade_ts < TRADE_COOLDOWN_SEC:
        return

    series = get_last_candles(SYMBOL, PRIMARY_INTERVAL, limit=ANALYSIS_LOOKBACK_1M)
    df = df_from_candles(series)

    bias, news_score = sentiment_bias(_cached_news)

    catalysts = {
        "bias": bias,
        "news_score": news_score,
        "macro_score": 0.4 if _cached_macro else 0
    }

    idea = build_trade_idea(df, catalysts, TRADE_MIN_CONF)

    if idea.direction == "NO_TRADE":
        return

    mid = sum(idea.entry)/2

    if _last_trade_dir == idea.direction and _last_trade_mid:
        if abs(mid - _last_trade_mid)/_last_trade_mid < TRADE_NOVELTY_PX:
            return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    msg = (
        f"🎯 *Gold Trade Idea — {idea.direction}* ({idea.confidence}/100)\n"
        f"Time: {now_utc}\n"
        f"Entry: {idea.entry[0]:.2f} → {idea.entry[1]:.2f}\n"
        f"SL: {idea.sl:.2f}\n"
        f"TP1: {idea.tp1:.2f} | TP2: {idea.tp2:.2f}\n"
        f"RR: {idea.rr:.2f}\n\n"
        + "\n".join([f"• {r}" for r in idea.reasons])
    )

    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")

    _last_trade_ts = now
    _last_trade_dir = idea.direction
    _last_trade_mid = mid


@app.on_event("startup")
async def startup():
    init_db()
    scheduler.add_job(job_fetch, "interval", seconds=FETCH_CANDLES_EVERY_SECONDS)
    scheduler.add_job(job_news, "interval", seconds=NEWS_POLL_EVERY_SECONDS)
    scheduler.add_job(job_macro, "interval", seconds=MACRO_POLL_EVERY_SECONDS)
    scheduler.add_job(job_signal, "interval", seconds=SIGNAL_EVAL_EVERY_SECONDS)
    scheduler.start()
