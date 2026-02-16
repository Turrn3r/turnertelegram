import os
import io
import logging
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

import httpx

from .storage import (
    init_db,
    upsert_candle,
    get_last_candles,
    insert_news_item,
    insert_flow_event,
    insert_orderbook_signal,
)
from .twelvedata import (
    SYMBOL_XRP,
    SYMBOL_GOLD,
    SYMBOL_SILVER,
    SYMBOL_OIL,
    fetch_time_series,
)
from .charting import CandleSeries, make_candlestick_png
from .news import fetch_news
from .orderbook import fetch_binance_depth, analyze_depth

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

# Order book proxy alerts (XRP on Binance)
ORDERBOOK_ALERTS = os.getenv("ORDERBOOK_ALERTS", "true").lower() in ("1", "true", "yes", "y")
OB_IMBALANCE_THRESHOLD = float(os.getenv("OB_IMBALANCE_THRESHOLD", "0.22"))  # 22% skew
OB_SPREAD_BPS_THRESHOLD = float(os.getenv("OB_SPREAD_BPS_THRESHOLD", "8"))   # wider spread = stress
OB_WALL_USD_THRESHOLD = float(os.getenv("OB_WALL_USD_THRESHOLD", "350000"))  # wall near mid

SYMBOLS = [SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER, SYMBOL_OIL]
SYMBOL_LABELS = {
    SYMBOL_XRP: "XRP / USD",
    SYMBOL_GOLD: "Gold (XAU) / USD",
    SYMBOL_SILVER: "Silver (XAG) / USD",
    SYMBOL_OIL: "Oil (USOIL)",
}

HAS_TELEGRAM = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID and POST_TO_TELEGRAM)


def _interval_human(interval: str) -> str:
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


async def _send_png_as_document(bot: Bot, filename: str, png_bytes: bytes, caption: str) -> None:
    # Sending as a document avoids Telegram photo compression and preserves zoom quality.
    f = io.BytesIO(png_bytes)
    f.name = filename
    await bot.send_document(chat_id=TELEGRAM_CHAT_ID, document=f, caption=caption, parse_mode="Markdown")


async def job_fetch_candles_and_post_charts() -> None:
    human_tf = _interval_human(CANDLE_INTERVAL)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    log.info("Cycle: candles interval=%s history=%d", CANDLE_INTERVAL, HISTORY_CANDLES)

    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/3.0"}) as client:
        for sym in SYMBOLS:
            try:
                candles = await fetch_time_series(client, sym, interval=CANDLE_INTERVAL, outputsize=HISTORY_CANDLES)
            except Exception as e:
                log.exception("TwelveData failed for %s: %s", sym, e)
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
            if len(series) < 10:
                continue

            last = series[-1]
            prev = series[-2]
            close = float(last["close"])
            prev_close = float(prev["close"])
            pct = _pct(close, prev_close)
            arrow = "🟢" if pct >= 0 else "🔴"

            title = f"{SYMBOL_LABELS.get(sym, sym)} — {human_tf}"
            subtitle = f"Close {_fmt_price(sym, close)} ({pct:+.2f}%) • Updated {now_utc} • Source: TwelveData"
            title_for_plot = f"{title}\n{subtitle}"

            png = make_candlestick_png(
                CandleSeries(symbol=sym, candles=series),
                title=title_for_plot,
                subtitle=subtitle,
                show_volume=False,
                dpi=420,  # high detail for zoom
            )

            caption = (
                f"🕯️ *{SYMBOL_LABELS.get(sym, sym)}*\n"
                f"*Timeframe:* `{human_tf}`\n"
                f"*Close:* `{_fmt_price(sym, close)}`  ({arrow} `{pct:+.2f}%`)\n"
                f"*Updated:* `{now_utc}`\n"
                "_Chart sent as document (lossless) for high-zoom pattern work._"
            )

            try:
                bot = await _bot()
                safe_name = sym.replace("/", "_").replace(" ", "_")
                await _send_png_as_document(bot, f"{safe_name}_{CANDLE_INTERVAL}.png", png, caption)
                log.info("Posted lossless chart for %s", sym)
            except Exception as e:
                log.exception("Telegram chart send failed for %s: %s", sym, e)


async def job_orderbook_alerts() -> None:
    if not (HAS_TELEGRAM and ORDERBOOK_ALERTS):
        return

    now = datetime.now(timezone.utc)
    now_utc = now.strftime("%Y-%m-%d %H:%M UTC")

    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/3.0"}) as client:
        try:
            depth = await fetch_binance_depth(client, symbol="XRPUSDT", limit=1000)
            sig = analyze_depth(depth, symbol="XRPUSDT", depth_pct_band=0.0025, wall_usd_threshold=OB_WALL_USD_THRESHOLD)
        except Exception as e:
            log.exception("Orderbook fetch/analyze failed: %s", e)
            return

    if not sig:
        return

    # Decide whether to alert
    stress = sig.spread_bps >= OB_SPREAD_BPS_THRESHOLD
    skew = abs(sig.imbalance) >= OB_IMBALANCE_THRESHOLD
    wall = sig.top_wall_side != "NONE"

    if not (stress or skew or wall):
        return

    # Build stable signal_id to dedupe noisy repeats (bucketed)
    bucket = f"{int(sig.mid*10000)}|{int(sig.spread_bps)}|{round(sig.imbalance,2)}|{sig.top_wall_side}|{int(sig.top_wall_usd/10000)}"
    signal_id = f"ob:{bucket}"

    if not insert_orderbook_signal(
        signal_id=signal_id,
        symbol=sig.symbol,
        mid=sig.mid,
        spread_bps=sig.spread_bps,
        bid_depth_usd=sig.bid_depth_usd,
        ask_depth_usd=sig.ask_depth_usd,
        imbalance=sig.imbalance,
        top_wall_side=sig.top_wall_side,
        top_wall_usd=sig.top_wall_usd,
        top_wall_price=sig.top_wall_price,
        source="binance_depth",
        ts=now,
    ):
        return

    direction = "🟢 Bid dominance" if sig.imbalance > 0 else "🔴 Ask dominance"
    msg = (
        "🏦 *Order Book / Liquidity Signal (XRPUSDT)*\n"
        f"*Time:* `{now_utc}`\n"
        f"*Mid:* `{sig.mid:.4f}`   *Spread:* `{sig.spread_bps:.2f} bps`\n"
        f"*Depth (±25bps):* Bid `{_fmt_notional(sig.bid_depth_usd)}` vs Ask `{_fmt_notional(sig.ask_depth_usd)}`\n"
        f"*Imbalance:* `{sig.imbalance:+.2f}`  ({direction})\n"
    )

    if sig.top_wall_side != "NONE":
        msg += f"*Wall:* `{sig.top_wall_side}` `{_fmt_notional(sig.top_wall_usd)}` @ `{sig.top_wall_price:.4f}`\n"

    msg += "\n_This is a liquidity/flow proxy (not identity-level institutional attribution)._"

    try:
        bot = await _bot()
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        log.exception("Orderbook telegram failed: %s", e)


async def job_news_alerts() -> None:
    if not (HAS_TELEGRAM and os.getenv("NEWS_ALERTS", "true").lower() in ("1", "true", "yes", "y")):
        return

    threshold = float(os.getenv("NEWS_THRESHOLD", "2.5"))
    include_neutral = os.getenv("NEWS_SEND_NEUTRAL", "false").lower() in ("1", "true", "yes", "y")
    max_items = int(os.getenv("NEWS_MAX_PER_CYCLE", "8"))

    items = fetch_news(threshold=threshold, include_neutral=include_neutral, max_items=max_items)
    if not items:
        return

    bot = await _bot()
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

        summary = (it.summary or "").strip()
        if len(summary) > 420:
            summary = summary[:420].rstrip() + "…"

        msg = (
            f"{_emoji(it.signal)} *International Macro / Government Alert*\n"
            f"*Signal:* `{it.signal}`   *Score:* `{it.score:+.2f}`\n"
            f"*Tags:* `{it.tags}`   *Source:* `{it.source}`\n"
            f"*Time:* `{now_utc}`\n\n"
            f"*Headline:* {it.title}\n"
        )
        if it.link:
            msg += f"{it.link}\n"
        if summary:
            msg += f"\n_{summary}_\n"
        msg += "\n_Disclaimer: automated scoring; verify impact._"

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown", disable_web_page_preview=False)
        except Exception as e:
            log.exception("News telegram failed: %s", e)


async def job_flow_alerts() -> None:
    if not (HAS_TELEGRAM and FLOW_ALERTS):
        return

    url = "https://api.binance.com/api/v3/aggTrades"
    params = {"symbol": "XRPUSDT", "limit": 200}

    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/3.0"}) as client:
        try:
            r = await client.get(url, params=params, timeout=20)
            r.raise_for_status()
            rows = r.json()
        except Exception as e:
            log.exception("Flow fetch failed: %s", e)
            return

    bot = await _bot()
    for row in rows:
        px = float(row["p"])
        qty = float(row["q"])
        notional = px * qty
        if notional < FLOW_NOTIONAL_THRESHOLD_USD:
            continue

        side = "SELL" if bool(row.get("m", False)) else "BUY"
        event_id = f"binance-{row['a']}"
        ts_utc = datetime.fromtimestamp(int(row["T"]) / 1000.0, tz=timezone.utc)

        if not insert_flow_event(event_id, SYMBOL_XRP, side, px, qty, notional, "binance_agg_trade", ts_utc):
            continue

        direction = "🟢 BUY pressure" if side == "BUY" else "🔴 SELL pressure"
        msg = (
            "🐋 *XRP Aggressive Flow Alert*\n"
            f"*Signal:* {direction}\n"
            f"*Notional:* `{_fmt_notional(notional)}`\n"
            f"*Price:* `{px:.4f}`\n"
            f"*Size:* `{qty:,.0f} XRP`\n"
            f"*Time:* `{ts_utc.isoformat()}`\n"
            "_Proxy via Binance aggTrades; not identity attribution._"
        )
        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
        except Exception as e:
            log.exception("Flow telegram failed: %s", e)


@app.on_event("startup")
async def startup():
    init_db()

    # Run once immediately
    try:
        await job_fetch_candles_and_post_charts()
    except Exception as e:
        log.exception("Startup charts failed: %s", e)

    scheduler.add_job(job_fetch_candles_and_post_charts, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
    scheduler.add_job(job_orderbook_alerts, "interval", minutes=1, max_instances=1, coalesce=True)  # faster OB scan
    scheduler.add_job(job_news_alerts, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
    scheduler.add_job(job_flow_alerts, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)

    scheduler.start()
    log.info("Scheduler started. HAS_TELEGRAM=%s", HAS_TELEGRAM)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    return {"ok": True, "telegram": HAS_TELEGRAM}
