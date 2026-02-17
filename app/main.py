import io
import os
import gc
import math
import time
import logging
from datetime import datetime, timezone
from collections import deque

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

import httpx
import pandas as pd

from .twelvedata import SYMBOL_XRP, fetch_time_series
from .charting import CandleSeries, make_candlestick_png
from .orderbook import fetch_binance_depth, analyze_depth, OrderBookSignal
from .analytics import structure_summary
from .signal_engine import build_trade_idea, TradeIdea
from .storage import init_db, upsert_candle, get_last_candles, insert_orderbook_signal

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("turnertelegram")

app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
scheduler = AsyncIOScheduler()

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID", "") or "").strip()
POST_TO_TELEGRAM = os.getenv("POST_TO_TELEGRAM", "true").lower() in ("1", "true", "yes", "y")
HAS_TELEGRAM = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID and POST_TO_TELEGRAM)

# Scheduling
FETCH_EVERY_MINUTES = int(os.getenv("FETCH_EVERY_MINUTES", "15"))

# XRP-only
SYMBOL = SYMBOL_XRP
LABEL = "XRP / USD"

# Candles
PRIMARY_INTERVAL = "1min"
PLOT_WINDOW_MINUTES = int(os.getenv("PLOT_WINDOW_MINUTES", "15"))      # plot last 15 x 1m candles
ANALYSIS_LOOKBACK_1M = int(os.getenv("ANALYSIS_LOOKBACK_1M", "360"))   # 6h of 1m candles for structure/ATR
CHART_DPI = int(os.getenv("CHART_DPI", "260"))

# Orderbook alerts (filtered + anomaly-based)
ORDERBOOK_ALERTS = os.getenv("ORDERBOOK_ALERTS", "true").lower() in ("1", "true", "yes", "y")
OB_WALL_USD_THRESHOLD = float(os.getenv("OB_WALL_USD_THRESHOLD", "350000"))
OB_CONFIRM_WINDOW = int(os.getenv("OB_CONFIRM_WINDOW", "5"))
OB_CONFIRM_HITS = int(os.getenv("OB_CONFIRM_HITS", "3"))
OB_COOLDOWN_SEC = int(os.getenv("OB_COOLDOWN_SEC", "900"))

# Trade ideas
TRADE_IDEAS = os.getenv("TRADE_IDEAS", "true").lower() in ("1", "true", "yes", "y")
TRADE_MIN_CONF = int(os.getenv("TRADE_MIN_CONF", "75"))
TRADE_COOLDOWN_SEC = int(os.getenv("TRADE_COOLDOWN_SEC", "1800"))  # 30 minutes
TRADE_NOVELTY_PX = float(os.getenv("TRADE_NOVELTY_PX", "0.0015"))   # 0.15% change required to re-alert

SEND_SYMBOL_ERRORS = os.getenv("SEND_SYMBOL_ERRORS", "true").lower() in ("1", "true", "yes", "y")

_last_ob: OrderBookSignal | None = None
_last_ob_alert_ts = 0.0
_last_ob_signature = None

_last_trade_ts = 0.0
_last_trade_dir = None
_last_trade_entry_mid = None

# rolling history for anomaly scoring
_OB_WINDOW = int(os.getenv("OB_WINDOW", "50"))
_ob_hist = deque(maxlen=_OB_WINDOW)


def _fmt_pct(x: float) -> str:
    return f"{x:+.2f}%"


def _fmt_price(px: float) -> str:
    return f"{px:,.4f}"


def _fmt_notional(x: float) -> str:
    if x >= 1_000_000_000:
        return f"${x/1_000_000_000:.2f}B"
    if x >= 1_000_000:
        return f"${x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x/1_000:.1f}K"
    return f"${x:,.0f}"


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    v = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(v)


def _zscore(value, xs):
    if len(xs) < 10:
        return 0.0
    s = _std(xs)
    if s <= 1e-12:
        return 0.0
    return (value - _mean(xs)) / s


def _confirm(flags_deque: deque, hits_required: int, window: int) -> bool:
    if len(flags_deque) < window:
        return False
    return sum(1 for x in list(flags_deque)[-window:] if x) >= hits_required


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


async def job_fetch_store_1m() -> None:
    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/1m"}) as client:
        try:
            candles = await fetch_time_series(client, SYMBOL, interval=PRIMARY_INTERVAL, outputsize=ANALYSIS_LOOKBACK_1M)
        except Exception as e:
            log.exception("TwelveData fetch failed sym=%s err=%s", SYMBOL, e)
            if HAS_TELEGRAM and SEND_SYMBOL_ERRORS:
                try:
                    bot = await _bot()
                    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"⚠️ XRP fetch failed: {e}")
                except Exception:
                    pass
            return

        for c in candles:
            upsert_candle(SYMBOL, PRIMARY_INTERVAL, c.t, c.open, c.high, c.low, c.close, c.volume, "twelvedata")


async def job_post_xrp_chart_and_rundown() -> None:
    if not HAS_TELEGRAM:
        return

    bot = await _bot()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    series = get_last_candles(SYMBOL, PRIMARY_INTERVAL, limit=max(ANALYSIS_LOOKBACK_1M, PLOT_WINDOW_MINUTES + 10))
    if len(series) < PLOT_WINDOW_MINUTES:
        return

    df_all = _df_from_candles(series)
    if df_all.empty or len(df_all) < 60:
        return

    plot_candles = series[-PLOT_WINDOW_MINUTES:]
    df_plot = _df_from_candles(plot_candles)
    if df_plot.empty:
        return

    ss = structure_summary(df_all)

    close = float(df_plot["Close"].iloc[-1])
    prev = float(df_plot["Close"].iloc[-2]) if len(df_plot) >= 2 else close
    ret = (close - prev) / prev * 100.0 if prev else 0.0

    title = f"{LABEL} — 1m candles (last {PLOT_WINDOW_MINUTES} mins)"
    footer = f"Updated {now_utc} • Close {_fmt_price(close)} ({_fmt_pct(ret)})"

    png = make_candlestick_png(
        CandleSeries(SYMBOL, plot_candles),
        title=f"{title}\n{footer}",
        footer=footer,
        dpi=CHART_DPI,
    )

    bos = ss.bos or "-"
    choch = ss.choch or "-"
    ph = f"{ss.last_pivot_high:.4f}" if ss.last_pivot_high else "-"
    pl = f"{ss.last_pivot_low:.4f}" if ss.last_pivot_low else "-"
    atr_info = f"{ss.atr:.6f}".rstrip("0").rstrip(".") if ss.atr is not None else "-"
    regime = ss.atr_regime

    caption = (
        f"*{LABEL}*\n"
        f"• *Close:* `{_fmt_price(close)}` (`{_fmt_pct(ret)}`)\n"
        f"• *Structure:* trend `{ss.trend}` | BOS `{bos}` | CHOCH `{choch}`\n"
        f"• *Pivots:* PH `{ph}` | PL `{pl}`\n"
        f"• *Volatility:* ATR `{atr_info}` | regime `{regime}`\n"
        f"• *Time:* `{now_utc}`\n"
        "_Lossless chart for zoom clarity._"
    )

    try:
        await _send_png_document(bot, f"xrp_1m_last{PLOT_WINDOW_MINUTES}.png", png, caption)
    finally:
        del png
        gc.collect()


async def job_orderbook_alerts() -> None:
    global _last_ob, _last_ob_alert_ts, _last_ob_signature, _ob_hist

    if not (HAS_TELEGRAM and ORDERBOOK_ALERTS):
        return

    now = datetime.now(timezone.utc)
    now_utc = now.strftime("%Y-%m-%d %H:%M UTC")
    now_ts = time.time()

    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/ob"}) as client:
        try:
            depth = await fetch_binance_depth(client, symbol="XRPUSDT", limit=1000)
            sig = analyze_depth(depth, symbol="XRPUSDT", depth_pct_band=0.0025, wall_usd_threshold=OB_WALL_USD_THRESHOLD, prev=_last_ob)
        except Exception as e:
            log.exception("Orderbook fetch/analyze failed: %s", e)
            return

    if not sig:
        return

    _ob_hist.append({
        "spread_bps": float(sig.spread_bps),
        "imbalance": float(sig.imbalance),
        "bid_depth": float(sig.bid_depth_usd),
        "ask_depth": float(sig.ask_depth_usd),
        "d_bid": float(sig.delta_bid_depth_usd),
        "d_ask": float(sig.delta_ask_depth_usd),
        "wall_side": sig.top_wall_side,
        "wall_usd": float(sig.top_wall_usd),
    })

    spreads = [x["spread_bps"] for x in _ob_hist]
    imbs = [x["imbalance"] for x in _ob_hist]
    bids = [x["bid_depth"] for x in _ob_hist]
    asks = [x["ask_depth"] for x in _ob_hist]

    z_spread = _zscore(sig.spread_bps, spreads)
    z_imb = _zscore(sig.imbalance, imbs)
    z_bid_depth = _zscore(sig.bid_depth_usd, bids)
    z_ask_depth = _zscore(sig.ask_depth_usd, asks)

    spread_stress = (z_spread >= 3.0)
    imbalance_shock = (abs(z_imb) >= 3.0)
    depth_vacuum = (z_bid_depth <= -2.5) or (z_ask_depth <= -2.5)
    wall = sig.top_wall_side != "NONE"

    if not hasattr(job_orderbook_alerts, "_recent"):
        job_orderbook_alerts._recent = {
            "spread": deque(maxlen=OB_CONFIRM_WINDOW),
            "imb": deque(maxlen=OB_CONFIRM_WINDOW),
            "vac": deque(maxlen=OB_CONFIRM_WINDOW),
            "wall": deque(maxlen=OB_CONFIRM_WINDOW),
        }
    r = job_orderbook_alerts._recent
    r["spread"].append(spread_stress)
    r["imb"].append(imbalance_shock)
    r["vac"].append(depth_vacuum)
    r["wall"].append(wall)

    confirmed = (
        _confirm(r["spread"], OB_CONFIRM_HITS, OB_CONFIRM_WINDOW) or
        _confirm(r["imb"], OB_CONFIRM_HITS, OB_CONFIRM_WINDOW) or
        _confirm(r["vac"], OB_CONFIRM_HITS, OB_CONFIRM_WINDOW) or
        _confirm(r["wall"], OB_CONFIRM_HITS, OB_CONFIRM_WINDOW)
    )

    major = depth_vacuum and spread_stress

    if not confirmed and not major:
        _last_ob = sig
        return

    signature = (
        round(sig.mid, 4),
        round(sig.spread_bps, 1),
        round(sig.imbalance, 2),
        sig.top_wall_side,
        int(sig.top_wall_usd / 50000) if sig.top_wall_usd else 0,
    )

    if not major:
        if (now_ts - _last_ob_alert_ts) < OB_COOLDOWN_SEC:
            _last_ob = sig
            return
        if signature == _last_ob_signature:
            _last_ob = sig
            return

    bucket = f"{int(sig.mid*10000)}|{int(sig.spread_bps)}|{round(sig.imbalance,2)}|{sig.top_wall_side}|{int(sig.top_wall_usd/10000)}"
    signal_id = f"ob:{bucket}"

    if not insert_orderbook_signal(
        signal_id, sig.symbol, sig.mid, sig.spread_bps, sig.bid_depth_usd, sig.ask_depth_usd,
        sig.imbalance, sig.top_wall_side, sig.top_wall_usd, sig.top_wall_price, "binance_depth", now
    ):
        _last_ob = sig
        return

    tags = []
    if major: tags.append("🔥 Major")
    if depth_vacuum: tags.append("🕳️ Vacuum")
    if spread_stress: tags.append("📏 Spread")
    if imbalance_shock: tags.append("📐 Imbalance")
    if wall: tags.append("🧱 Wall")

    msg = (
        f"🏦 *Order Book Alert (XRPUSDT)* — {', '.join(tags) if tags else 'Signal'}\n"
        f"• *Time:* `{now_utc}`\n"
        f"• *Mid:* `{sig.mid:.4f}` | *Spread:* `{sig.spread_bps:.2f} bps` (z `{z_spread:+.1f}`)\n"
        f"• *Depth:* Bid `{_fmt_notional(sig.bid_depth_usd)}` (z `{z_bid_depth:+.1f}`) vs Ask `{_fmt_notional(sig.ask_depth_usd)}` (z `{z_ask_depth:+.1f}`)\n"
        f"• *Imbalance:* `{sig.imbalance:+.2f}` (z `{z_imb:+.1f}`)\n"
    )
    if wall:
        msg += f"• *Wall:* `{sig.top_wall_side}` `{_fmt_notional(sig.top_wall_usd)}` @ `{sig.top_wall_price:.4f}`\n"
    msg += "\n_Filtered: anomaly + persistence + cooldown._"

    bot = await _bot()
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
    _last_ob_alert_ts = now_ts
    _last_ob_signature = signature
    _last_ob = sig


async def job_trade_ideas() -> None:
    """
    Uses candles + latest orderbook signal to propose Entry/SL/TP.
    Sends only on high confidence + cooldown + novelty.
    """
    global _last_trade_ts, _last_trade_dir, _last_trade_entry_mid

    if not (HAS_TELEGRAM and TRADE_IDEAS):
        return

    now_ts = time.time()
    if (now_ts - _last_trade_ts) < TRADE_COOLDOWN_SEC:
        return

    series = get_last_candles(SYMBOL, PRIMARY_INTERVAL, limit=ANALYSIS_LOOKBACK_1M)
    df = _df_from_candles(series)
    if df.empty or len(df) < 120:
        return

    # Build OB features from last observed orderbook snapshot
    ob = None
    if _last_ob is not None:
        ob = {
            "imbalance": float(_last_ob.imbalance),
            "spread_bps": float(_last_ob.spread_bps),
            "top_wall_side": _last_ob.top_wall_side,
            "top_wall_usd": float(_last_ob.top_wall_usd),
            "delta_bid_depth_usd": float(_last_ob.delta_bid_depth_usd),
            "delta_ask_depth_usd": float(_last_ob.delta_ask_depth_usd),
        }

    idea: TradeIdea = build_trade_idea(df, ob, min_confidence=TRADE_MIN_CONF)
    if idea.direction == "NO_TRADE":
        return

    entry_mid = sum(idea.entry) / 2 if idea.entry else None

    # novelty gating: if same direction + entry is basically same, skip
    if _last_trade_dir == idea.direction and _last_trade_entry_mid and entry_mid:
        if abs(entry_mid - _last_trade_entry_mid) / max(_last_trade_entry_mid, 1e-12) < TRADE_NOVELTY_PX:
            return

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    reasons = "\n".join([f"• {r}" for r in idea.reasons[:8]])
    msg = (
        f"🎯 *Trade Idea (XRP)* — *{idea.direction}*  (confidence `{idea.confidence}/100`)\n"
        f"• *Time:* `{now_utc}`\n"
        f"• *Entry zone:* `{idea.entry[0]:.4f}` → `{idea.entry[1]:.4f}`\n"
        f"• *SL:* `{idea.sl:.4f}`\n"
        f"• *TP1:* `{idea.tp1:.4f}` | *TP2:* `{idea.tp2:.4f}`\n"
        + (f"• *RR (to TP1):* `{idea.rr:.2f}`\n" if idea.rr is not None else "")
        + "\n*Why:*\n"
        f"{reasons}\n\n"
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

    scheduler.add_job(job_fetch_store_1m, "interval", minutes=1, max_instances=1, coalesce=True)
    scheduler.add_job(job_post_xrp_chart_and_rundown, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
    scheduler.add_job(job_orderbook_alerts, "interval", seconds=30, max_instances=1, coalesce=True)
    scheduler.add_job(job_trade_ideas, "interval", seconds=60, max_instances=1, coalesce=True)

    scheduler.start()
    log.info("Scheduler started. XRP-only. HAS_TELEGRAM=%s", HAS_TELEGRAM)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "ok": True})


@app.get("/health")
async def health():
    return {
        "ok": True,
        "telegram": HAS_TELEGRAM,
        "symbol": LABEL,
        "interval": PRIMARY_INTERVAL,
        "plot_window_minutes": PLOT_WINDOW_MINUTES,
        "analysis_lookback_1m": ANALYSIS_LOOKBACK_1M,
        "trade_ideas": {
            "enabled": TRADE_IDEAS,
            "min_conf": TRADE_MIN_CONF,
            "cooldown_sec": TRADE_COOLDOWN_SEC,
            "novelty_px": TRADE_NOVELTY_PX,
        },
        "orderbook": {
            "enabled": ORDERBOOK_ALERTS,
            "cooldown_sec": OB_COOLDOWN_SEC,
            "confirm_window": OB_CONFIRM_WINDOW,
            "confirm_hits": OB_CONFIRM_HITS,
            "rolling_window": _OB_WINDOW,
        }
    }
