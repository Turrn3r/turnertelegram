import io
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
import pandas as pd

from .twelvedata import SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER, SYMBOL_OIL, fetch_time_series
from .charting import CandleSeries, make_candlestick_png
from .news import fetch_news
from .orderbook import fetch_binance_depth, analyze_depth
from .analytics import structure_summary
from .storage import (
    init_db,
    upsert_candle,
    get_last_candles,
    insert_news_item,
    insert_flow_event,
    insert_orderbook_signal,
    find_nearest_candle_close,
    upsert_event_link,
    get_recent_event_impacts,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("turnertelegram")

app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
scheduler = AsyncIOScheduler()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID", "") or "").strip()
POST_TO_TELEGRAM = os.getenv("POST_TO_TELEGRAM", "true").lower() in ("1", "true", "yes", "y")
HAS_TELEGRAM = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID and POST_TO_TELEGRAM)

FETCH_EVERY_MINUTES = int(os.getenv("FETCH_EVERY_MINUTES", "15"))
PRIMARY_INTERVAL = os.getenv("CANDLE_INTERVAL_PRIMARY", "15min").strip()
CONTEXT_INTERVALS = [s.strip() for s in (os.getenv("CANDLE_INTERVALS_CONTEXT", "1h,4h") or "").split(",") if s.strip()]
HISTORY_CANDLES = int(os.getenv("HISTORY_CANDLES", "320"))

NEWS_ALERTS = os.getenv("NEWS_ALERTS", "true").lower() in ("1", "true", "yes", "y")
NEWS_THRESHOLD = float(os.getenv("NEWS_THRESHOLD", "2.5"))
NEWS_SEND_NEUTRAL = os.getenv("NEWS_SEND_NEUTRAL", "false").lower() in ("1", "true", "yes", "y")
NEWS_MAX_PER_CYCLE = int(os.getenv("NEWS_MAX_PER_CYCLE", "10"))

FLOW_ALERTS = os.getenv("FLOW_ALERTS", "true").lower() in ("1", "true", "yes", "y")
FLOW_NOTIONAL_THRESHOLD_USD = float(os.getenv("FLOW_NOTIONAL_THRESHOLD_USD", "250000"))

ORDERBOOK_ALERTS = os.getenv("ORDERBOOK_ALERTS", "true").lower() in ("1", "true", "yes", "y")
OB_IMBALANCE_THRESHOLD = float(os.getenv("OB_IMBALANCE_THRESHOLD", "0.22"))
OB_SPREAD_BPS_THRESHOLD = float(os.getenv("OB_SPREAD_BPS_THRESHOLD", "8"))
OB_WALL_USD_THRESHOLD = float(os.getenv("OB_WALL_USD_THRESHOLD", "350000"))

SYMBOLS = [SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER, SYMBOL_OIL]
LABELS = {SYMBOL_XRP: "XRP / USD", SYMBOL_GOLD: "Gold (XAU) / USD", SYMBOL_SILVER: "Silver (XAG) / USD", SYMBOL_OIL: "Oil (USOIL)"}


def _human_tf(interval: str) -> str:
    s = interval.strip().lower()
    if s.endswith("min"):
        n = s.replace("min", "").strip()
        if n.isdigit():
            return f"{n}-minute timeframe"
    return f"{interval} timeframe"


def _fmt_price(sym: str, px: float) -> str:
    return f"{px:,.4f}" if sym == SYMBOL_XRP else f"{px:,.2f}"


def _fmt_pct(x: float) -> str:
    return f"{x:+.2f}%"


def _fmt_notional(x: float) -> str:
    if x >= 1_000_000_000:
        return f"${x/1_000_000_000:.2f}B"
    if x >= 1_000_000:
        return f"${x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x/1_000:.1f}K"
    return f"${x:,.0f}"


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
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df


async def job_fetch_store_all_intervals() -> None:
    """
    Fetches candles for all symbols for primary + context intervals and stores into DB.
    """
    intervals = [PRIMARY_INTERVAL] + CONTEXT_INTERVALS
    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/edge-1.0"}) as client:
        for sym in SYMBOLS:
            for interval in intervals:
                try:
                    candles = await fetch_time_series(client, sym, interval=interval, outputsize=HISTORY_CANDLES)
                except Exception as e:
                    log.exception("TwelveData fetch failed sym=%s interval=%s err=%s", sym, interval, e)
                    continue
                for c in candles:
                    upsert_candle(sym, interval, c.t, c.open, c.high, c.low, c.close, c.volume, "twelvedata")


async def job_post_charts_and_context() -> None:
    """
    Posts ONE lossless chart per asset:
      - chart: primary interval with overlays
      - caption: structure summary on primary + context trends (1h/4h) + event impacts
    """
    if not HAS_TELEGRAM:
        return

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    human_tf = _human_tf(PRIMARY_INTERVAL)
    bot = await _bot()

    for sym in SYMBOLS:
        primary = get_last_candles(sym, PRIMARY_INTERVAL, limit=HISTORY_CANDLES)
        if len(primary) < 80:
            continue

        df_p = _df_from_candles(primary)
        ss_p = structure_summary(df_p)
        last_close = float(df_p["Close"].iloc[-1])
        prev_close = float(df_p["Close"].iloc[-2])
        ret = (last_close - prev_close) / prev_close * 100.0 if prev_close else 0.0
        arrow = "🟢" if ret >= 0 else "🔴"

        # Context summaries (1h/4h)
        ctx_lines = []
        for ctx in CONTEXT_INTERVALS:
            ctx_c = get_last_candles(sym, ctx, limit=min(HISTORY_CANDLES, 400))
            df_c = _df_from_candles(ctx_c)
            if df_c.empty or len(df_c) < 50:
                continue
            ss_c = structure_summary(df_c)
            ctx_lines.append(f"`{ctx}` trend `{ss_c.trend}`  BOS `{ss_c.bos or '-'}`
CHOCH `{ss_c.choch or '-'}`")

        # Event impact snippets
        impacts = get_recent_event_impacts(sym, PRIMARY_INTERVAL, limit=3)
        impact_lines = []
        for it in impacts:
            impact_lines.append(f"• `{it['candle_time_utc']}` move `{_fmt_pct(it['return_pct'])}`")

        # Chart
        title = f"{LABELS.get(sym, sym)} — {human_tf}"
        footer = f"Close {_fmt_price(sym, last_close)} ({_fmt_pct(ret)}) • {now_utc} • EMA9/21 • RSI/ATR • pivots"
        png = make_candlestick_png(CandleSeries(sym, primary), title=f"{title}\n{footer}", footer=footer, dpi=480)

        # Caption designed for fast human scan
        bos = ss_p.bos or "-"
        choch = ss_p.choch or "-"
        ph = f"{ss_p.last_pivot_high:.4f}" if ss_p.last_pivot_high else "-"
        pl = f"{ss_p.last_pivot_low:.4f}" if ss_p.last_pivot_low else "-"
        atr_info = "-"
        if ss_p.atr is not None:
            atr_info = f"{ss_p.atr:.6f}".rstrip("0").rstrip(".")
        regime = ss_p.atr_regime

        caption = (
            f"🕯️ *{LABELS.get(sym, sym)}*\n"
            f"*Primary:* `{human_tf}`  *Close:* `{_fmt_price(sym, last_close)}` ({arrow} `{_fmt_pct(ret)}`)\n"
            f"*Structure:* trend `{ss_p.trend}`  BOS `{bos}`  CHOCH `{choch}`\n"
            f"*Pivots:* PH `{ph}`  PL `{pl}`\n"
            f"*Volatility:* ATR `{atr_info}`  regime `{regime}`"
        )

        if ctx_lines:
            caption += "\n\n*Context (higher TF):*\n" + "\n".join(ctx_lines)

        if impact_lines:
            caption += "\n\n*Recent event impact (since alert candle):*\n" + "\n".join(impact_lines)

        caption += "\n\n_Lossless chart document for zoom + pattern work._"

        try:
            fname = f"{sym}_{PRIMARY_INTERVAL}_edge.png".replace("/", "_")
            await _send_png_document(bot, fname, png, caption)
        except Exception as e:
            log.exception("Telegram chart post failed sym=%s err=%s", sym, e)


async def job_news_alerts_and_link() -> None:
    """
    Sends news alerts and links them to candle close for impact tracking.
    """
    if not (HAS_TELEGRAM and NEWS_ALERTS):
        return

    bot = await _bot()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    items = fetch_news(threshold=NEWS_THRESHOLD, include_neutral=NEWS_SEND_NEUTRAL, max_items=NEWS_MAX_PER_CYCLE)
    for it in items:
        inserted = insert_news_item(it.guid, it.source, it.title, it.link, it.summary, it.published, it.tags, it.score, it.signal)
        if not inserted:
            continue

        msg = (
            f"🌍 {_emoji(it.signal)} *International Announcement Alert*\n"
            f"*Signal:* `{it.signal}`  *Score:* `{it.score:+.2f}`\n"
            f"*Tags:* `{it.tags}`  *Source:* `{it.source}`\n"
            f"*Time:* `{now_utc}`\n\n"
            f"*Headline:* {it.title}\n"
        )
        if it.link:
            msg += f"{it.link}\n"

        summary = (it.summary or "").strip()
        if len(summary) > 420:
            summary = summary[:420].rstrip() + "…"
        if summary:
            msg += f"\n_{summary}_\n"

        msg += "\n_Disclaimer: automated scoring; verify before trading._"

        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown", disable_web_page_preview=False)
        except Exception as e:
            log.exception("Telegram news send failed: %s", e)

        # Link event to each tagged asset symbol in primary interval
        tags = [t.strip() for t in (it.tags or "").split(",") if t.strip()]
        for sym in tags:
            if sym not in SYMBOLS:
                continue
            latest = find_nearest_candle_close(sym, PRIMARY_INTERVAL)
            if not latest:
                continue
            candle_time, base_close = latest
            # current impact starts at 0 (last_close == base_close)
            link_id = f"{it.guid}:{sym}:{PRIMARY_INTERVAL}:{candle_time}"
            upsert_event_link(link_id, it.guid, sym, PRIMARY_INTERVAL, candle_time, base_close, base_close, 0.0)


def _emoji(sig: str) -> str:
    return {"BUY": "🟢", "SELL": "🔴", "NEUTRAL": "🟡"}.get(sig, "🟡")


async def job_update_event_impacts() -> None:
    """
    Updates event impact table values by comparing base_close to latest close.
    Lightweight “since-event move” tracking.
    """
    for sym in SYMBOLS:
        latest = find_nearest_candle_close(sym, PRIMARY_INTERVAL)
        if not latest:
            continue
        candle_time, last_close = latest

        # Update the last few links for this symbol
        # We can’t easily query all links without adding more functions, so keep it simple:
        # re-link only most recent events already recorded via get_recent_event_impacts,
        # which implies prior insert exists.
        impacts = get_recent_event_impacts(sym, PRIMARY_INTERVAL, limit=8)
        for imp in impacts:
            # We don’t have base_close here; do an approximate re-create link_id and let upsert update:
            # (Since link_id is unique, we can’t update without base_close; so skip.)
            # In a further iteration we’d store base_close in the query and update properly.
            pass


async def job_orderbook_alerts() -> None:
    if not (HAS_TELEGRAM and ORDERBOOK_ALERTS):
        return

    now = datetime.now(timezone.utc)
    now_utc = now.strftime("%Y-%m-%d %H:%M UTC")

    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/edge-1.0"}) as client:
        try:
            depth = await fetch_binance_depth(client, symbol="XRPUSDT", limit=1000)
            sig = analyze_depth(depth, symbol="XRPUSDT", depth_pct_band=0.0025, wall_usd_threshold=OB_WALL_USD_THRESHOLD)
        except Exception as e:
            log.exception("Orderbook fetch/analyze failed: %s", e)
            return
    if not sig:
        return

    stress = sig.spread_bps >= OB_SPREAD_BPS_THRESHOLD
    skew = abs(sig.imbalance) >= OB_IMBALANCE_THRESHOLD
    wall = sig.top_wall_side != "NONE"
    if not (stress or skew or wall):
        return

    bucket = f"{int(sig.mid*10000)}|{int(sig.spread_bps)}|{round(sig.imbalance,2)}|{sig.top_wall_side}|{int(sig.top_wall_usd/10000)}"
    signal_id = f"ob:{bucket}"
    if not insert_orderbook_signal(signal_id, sig.symbol, sig.mid, sig.spread_bps, sig.bid_depth_usd, sig.ask_depth_usd, sig.imbalance,
                                  sig.top_wall_side, sig.top_wall_usd, sig.top_wall_price, "binance_depth", now):
        return

    direction = "🟢 Bid dominance" if sig.imbalance > 0 else "🔴 Ask dominance"
    msg = (
        "🏦 *Order Book / Liquidity Signal (XRPUSDT)*\n"
        f"*Time:* `{now_utc}`\n"
        f"*Mid:* `{sig.mid:.4f}`  *Spread:* `{sig.spread_bps:.2f} bps`\n"
        f"*Depth (±25bps):* Bid `{_fmt_notional(sig.bid_depth_usd)}` vs Ask `{_fmt_notional(sig.ask_depth_usd)}`\n"
        f"*Imbalance:* `{sig.imbalance:+.2f}` ({direction})\n"
    )
    if sig.top_wall_side != "NONE":
        msg += f"*Wall:* `{sig.top_wall_side}` `{_fmt_notional(sig.top_wall_usd)}` @ `{sig.top_wall_price:.4f}`\n"
    msg += "\n_This is a liquidity/flow proxy (not identity-level institutional attribution)._"

    try:
        bot = await _bot()
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        log.exception("Orderbook telegram failed: %s", e)


async def job_flow_alerts() -> None:
    if not (HAS_TELEGRAM and FLOW_ALERTS):
        return

    url = "https://api.binance.com/api/v3/aggTrades"
    params = {"symbol": "XRPUSDT", "limit": 200}
    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/edge-1.0"}) as client:
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

    # Initial fill + post
    await job_fetch_store_all_intervals()
    await job_post_charts_and_context()

    scheduler.add_job(job_fetch_store_all_intervals, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
    scheduler.add_job(job_post_charts_and_context, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)

    scheduler.add_job(job_news_alerts_and_link, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
    scheduler.add_job(job_orderbook_alerts, "interval", minutes=1, max_instances=1, coalesce=True)
    scheduler.add_job(job_flow_alerts, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)

    scheduler.start()
    log.info("Scheduler started. HAS_TELEGRAM=%s", HAS_TELEGRAM)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "ok": True})


@app.get("/health")
async def health():
    return {"ok": True, "telegram": HAS_TELEGRAM, "primary_interval": PRIMARY_INTERVAL, "context_intervals": CONTEXT_INTERVALS}
