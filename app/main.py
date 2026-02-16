import os
import logging
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot
import httpx

from .storage import init_db, upsert_candle, get_last_candles, insert_news_item, insert_flow_event
from .twelvedata import (
    SYMBOL_XRP,
    SYMBOL_GOLD,
    SYMBOL_SILVER,
    SYMBOL_OIL,
    fetch_time_series,
)
from .charting import CandleSeries, make_candlestick_png
from .news import fetch_news

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("turnertelegram")

app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
scheduler = AsyncIOScheduler()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID", "") or "").strip()
POST_TO_TELEGRAM = os.getenv("POST_TO_TELEGRAM", "true").lower() in ("1", "true", "yes", "y")

FETCH_EVERY_MINUTES = int(os.getenv("FETCH_EVERY_MINUTES", "15"))
CANDLE_INTERVAL = os.getenv("CANDLE_INTERVAL", "15min").strip()
HISTORY_CANDLES = int(os.getenv("HISTORY_CANDLES", "300"))

NEWS_ALERTS = os.getenv("NEWS_ALERTS", "true").lower() in ("1", "true", "yes", "y")
NEWS_THRESHOLD = float(os.getenv("NEWS_THRESHOLD", "2.5"))
NEWS_SEND_NEUTRAL = os.getenv("NEWS_SEND_NEUTRAL", "false").lower() in ("1", "true", "yes", "y")
NEWS_MAX_PER_CYCLE = int(os.getenv("NEWS_MAX_PER_CYCLE", "8"))

FLOW_ALERTS = os.getenv("FLOW_ALERTS", "true").lower() in ("1", "true", "yes", "y")
FLOW_NOTIONAL_THRESHOLD_USD = float(os.getenv("FLOW_NOTIONAL_THRESHOLD_USD", "250000"))

SYMBOLS = [SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER, SYMBOL_OIL]

SYMBOL_LABELS = {
    SYMBOL_XRP: "XRP / USD",
    SYMBOL_GOLD: "Gold (XAU) / USD",
    SYMBOL_SILVER: "Silver (XAG) / USD",
    SYMBOL_OIL: "Oil (USOIL)",
}

HAS_TELEGRAM = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID and POST_TO_TELEGRAM)


def _interval_human(interval: str) -> str:
    # Make it human: "15min" -> "15-minute timeframe"
    s = interval.strip().lower()
    if s.endswith("min"):
        n = s.replace("min", "").strip()
        if n.isdigit():
            return f"{n}-minute timeframe"
    if s.endswith("h"):
        return f"{s} timeframe"
    return f"{interval} timeframe"


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def _fmt_price(sym: str, px: float) -> str:
    return f"{px:,.4f}" if sym == SYMBOL_XRP else f"{px:,.2f}"


def _emoji(sig: str) -> str:
    return {"BUY": "🟢", "SELL": "🔴", "NEUTRAL": "🟡"}.get(sig, "🟡")


def _fmt_notional(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value/1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value/1_000:.1f}K"
    return f"${value:,.0f}"


async def _bot() -> Bot:
    return Bot(token=TELEGRAM_BOT_TOKEN)


async def job_fetch_candles_and_post_charts() -> None:
    """
    Every cycle:
      - Pull OHLC candles from TwelveData (15min timeframe)
      - Upsert into DB
      - Post 1 dark-theme candlestick chart per symbol (4 posts)
    """
    human_tf = _interval_human(CANDLE_INTERVAL)
    log.info("Cycle start: interval=%s (%s) candles=%d", CANDLE_INTERVAL, human_tf, HISTORY_CANDLES)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/2.0"}) as client:
        for sym in SYMBOLS:
            try:
                candles = await fetch_time_series(client, sym, interval=CANDLE_INTERVAL, outputsize=HISTORY_CANDLES)
            except Exception as e:
                log.exception("TwelveData fetch failed for %s: %s", sym, e)
                continue

            for c in candles:
                upsert_candle(
                    symbol=sym,
                    interval=CANDLE_INTERVAL,
                    open_time_utc=c.t,
                    o=c.open,
                    h=c.high,
                    l=c.low,
                    c=c.close,
                    v=c.volume,
                    source="twelvedata",
                )

            if not HAS_TELEGRAM:
                continue

            series = get_last_candles(sym, CANDLE_INTERVAL, limit=HISTORY_CANDLES)
            if len(series) < 5:
                log.warning("Not enough candles for %s yet", sym)
                continue

            last = series[-1]
            prev = series[-2]
            close = float(last["close"])
            prev_close = float(prev["close"])
            pct = _pct(close, prev_close)

            # Make the title “human pattern recognition” friendly:
            # - symbol big & clear
            # - timeframe explicit
            # - include last update time
            arrow = "🟢" if pct >= 0 else "🔴"
            title = f"{SYMBOL_LABELS.get(sym, sym)} — {human_tf}"
            subtitle = f"Close {_fmt_price(sym, close)}  ({arrow} {pct:+.2f}%)   •   Updated {now_utc}"

            # Put subtitle into title line for Telegram clarity (since mplfinance lacks true subtitle)
            title_for_plot = f"{title}\n{subtitle}"

            png = make_candlestick_png(
                CandleSeries(symbol=sym, candles=series),
                title=title_for_plot,
                subtitle=subtitle,
                show_volume=False,
            )

            caption = (
                f"🕯️ *{SYMBOL_LABELS.get(sym, sym)}*\n"
                f"*Timeframe:* `{human_tf}`\n"
                f"*Close:* `{_fmt_price(sym, close)}`  ({arrow} `{pct:+.2f}%`)\n"
                f"*Updated:* `{now_utc}`\n"
                "_Dark theme chart optimized for pattern recognition._"
            )

            try:
                bot = await _bot()
                await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=png, caption=caption, parse_mode="Markdown")
                log.info("Posted chart for %s", sym)
            except Exception as e:
                log.exception("Telegram send_photo failed for %s: %s", sym, e)

    log.info("Cycle end: charts")


async def job_news_alerts() -> None:
    if not (HAS_TELEGRAM and NEWS_ALERTS):
        return

    items = fetch_news(
        threshold=NEWS_THRESHOLD,
        include_neutral=NEWS_SEND_NEUTRAL,
        max_items=NEWS_MAX_PER_CYCLE,
    )
    if not items:
        return

    bot = await _bot()
    sent = 0
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for it in items:
        if not insert_news_item(
            guid=it.guid,
            source=it.source,
            title=it.title,
            link=it.link,
            summary=it.summary,
            published=it.published,
            tags=it.tags,
            score=it.score,
            signal=it.signal,
        ):
            continue

        # Clean, fast-to-scan formatting
        msg = (
            f"{_emoji(it.signal)} *International Macro / Government Alert*\n"
            f"*Signal:* `{it.signal}`   *Score:* `{it.score:+.2f}`\n"
            f"*Tags:* `{it.tags}`   *Source:* `{it.source}`\n"
            f"*Time:* `{now_utc}`\n\n"
            f"*Headline:* {it.title}\n"
        )
        if it.link:
            msg += f"{it.link}\n"

        if it.summary:
            summary = it.summary
            if len(summary) > 350:
                summary = summary[:350].rstrip() + "…"
            msg += f"\n_{summary}_\n"

        msg += "\n_Disclaimer: automated scoring. Verify impact before trading._"

        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=msg,
                parse_mode="Markdown",
                disable_web_page_preview=False,
            )
            sent += 1
        except Exception as e:
            log.exception("Telegram send_message failed: %s", e)

    if sent:
        log.info("News alerts sent=%d", sent)


async def job_flow_alerts() -> None:
    if not (HAS_TELEGRAM and FLOW_ALERTS):
        return

    url = "https://api.binance.com/api/v3/aggTrades"
    params = {"symbol": "XRPUSDT", "limit": 200}

    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/2.0"}) as client:
        try:
            r = await client.get(url, params=params, timeout=20)
            r.raise_for_status()
            rows = r.json()
        except Exception as e:
            log.exception("Flow fetch failed: %s", e)
            return

    bot = await _bot()
    sent = 0

    for row in rows:
        px = float(row["p"])
        qty = float(row["q"])
        notional = px * qty
        if notional < FLOW_NOTIONAL_THRESHOLD_USD:
            continue

        side = "SELL" if bool(row.get("m", False)) else "BUY"
        event_id = f"binance-{row['a']}"
        ts_utc = datetime.fromtimestamp(int(row["T"]) / 1000.0, tz=timezone.utc)

        if not insert_flow_event(
            event_id=event_id,
            symbol=SYMBOL_XRP,
            side=side,
            price=px,
            quantity=qty,
            notional_usd=notional,
            source="binance_agg_trade",
            ts=ts_utc,
        ):
            continue

        direction = "🟢 BUY pressure" if side == "BUY" else "🔴 SELL pressure"
        msg = (
            "🐋 *XRP Large-Flow Alert*\n"
            f"*Signal:* {direction}\n"
            f"*Notional:* `{_fmt_notional(notional)}`\n"
            f"*Price:* `{_fmt_price(SYMBOL_XRP, px)}`\n"
            f"*Size:* `{qty:,.0f} XRP`\n"
            f"*Time:* `{ts_utc.isoformat()}`\n"
            "_Source: Binance aggTrades (proxy)_"
        )

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
            sent += 1
        except Exception as e:
            log.exception("Flow telegram failed: %s", e)

    if sent:
        log.info("Flow alerts sent=%d", sent)


@app.on_event("startup")
async def startup():
    init_db()

    # immediate run
    try:
        await job_fetch_candles_and_post_charts()
    except Exception as e:
        log.exception("Startup chart cycle failed: %s", e)

    scheduler.add_job(job_fetch_candles_and_post_charts, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
    scheduler.add_job(job_news_alerts, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
    scheduler.add_job(job_flow_alerts, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)

    scheduler.start()
    log.info("Scheduler started. interval=%d HAS_TELEGRAM=%s", FETCH_EVERY_MINUTES, HAS_TELEGRAM)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "symbols": SYMBOLS,
            "interval": CANDLE_INTERVAL,
            "telegram": HAS_TELEGRAM,
            "extra_feeds": bool((os.getenv("EXTRA_RSS_FEEDS", "") or "").strip()),
        },
    )


@app.get("/health")
async def health():
    return {"ok": True, "telegram": HAS_TELEGRAM, "interval": CANDLE_INTERVAL}
