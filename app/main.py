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

from .twelvedata import SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER, fetch_time_series
from .charting import CandleSeries, make_candlestick_png
from .orderbook import fetch_binance_depth, analyze_depth, OrderBookSignal
from .analytics import structure_summary
from .storage import (
    init_db,
    upsert_candle,
    get_last_candles,
    insert_orderbook_signal,
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

# Requirement: 1-minute candles over last 15 minutes
PRIMARY_INTERVAL = "1min"
PLOT_WINDOW_MINUTES = int(os.getenv("PLOT_WINDOW_MINUTES", "15"))      # plot only last 15 1m candles
ANALYSIS_LOOKBACK_1M = int(os.getenv("ANALYSIS_LOOKBACK_1M", "240"))   # use 4h 1m candles for pivots/structure
CHART_DPI = int(os.getenv("CHART_DPI", "240"))

# Orderbook alerting (upgraded)
ORDERBOOK_ALERTS = os.getenv("ORDERBOOK_ALERTS", "true").lower() in ("1", "true", "yes", "y")

# old baseline thresholds (still used, but now combined with z-score gating)
OB_IMBALANCE_THRESHOLD = float(os.getenv("OB_IMBALANCE_THRESHOLD", "0.22"))
OB_SPREAD_BPS_THRESHOLD = float(os.getenv("OB_SPREAD_BPS_THRESHOLD", "8"))
OB_WALL_USD_THRESHOLD = float(os.getenv("OB_WALL_USD_THRESHOLD", "350000"))
OB_DEPTH_DELTA_USD = float(os.getenv("OB_DEPTH_DELTA_USD", "250000"))

# rolling stats window for z-scores
_OB_WINDOW = int(os.getenv("OB_WINDOW", "40"))  # 40 samples @ 30s ≈ 20 minutes
_ob_hist = deque(maxlen=_OB_WINDOW)

# persistence: require N hits in last M samples
OB_CONFIRM_WINDOW = int(os.getenv("OB_CONFIRM_WINDOW", "5"))
OB_CONFIRM_HITS = int(os.getenv("OB_CONFIRM_HITS", "3"))

# cooldown seconds (quiet the bot)
OB_COOLDOWN_SEC = int(os.getenv("OB_COOLDOWN_SEC", "600"))  # 10 minutes default

SEND_SYMBOL_ERRORS = os.getenv("SEND_SYMBOL_ERRORS", "true").lower() in ("1", "true", "yes", "y")

# ✅ OIL REMOVED
SYMBOLS = [SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER]
LABELS = {
    SYMBOL_XRP: "XRP / USD",
    SYMBOL_GOLD: "Gold (XAU) / USD",
    SYMBOL_SILVER: "Silver (XAG) / USD",
}

_last_ob: OrderBookSignal | None = None
_last_ob_alert_ts = 0.0
_last_ob_signature = None


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


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    v = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(v)


def _zscore(value, xs):
    # wait for enough samples
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
        for sym in SYMBOLS:
            try:
                candles = await fetch_time_series(
                    client,
                    sym,
                    interval=PRIMARY_INTERVAL,
                    outputsize=ANALYSIS_LOOKBACK_1M
                )
            except Exception as e:
                log.exception("TwelveData fetch failed sym=%s interval=1min err=%s", sym, e)
                if HAS_TELEGRAM and SEND_SYMBOL_ERRORS:
                    try:
                        bot = await _bot()
                        await bot.send_message(
                            chat_id=TELEGRAM_CHAT_ID,
                            text=f"⚠️ Data fetch failed for {LABELS.get(sym,sym)} (requested 1m).\nError: {e}",
                        )
                    except Exception:
                        pass
                continue

            for c in candles:
                upsert_candle(sym, PRIMARY_INTERVAL, c.t, c.open, c.high, c.low, c.close, c.volume, "twelvedata")


async def job_post_1m_last15_charts() -> None:
    if not HAS_TELEGRAM:
        return

    bot = await _bot()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for sym in SYMBOLS:
        series = get_last_candles(sym, PRIMARY_INTERVAL, limit=max(ANALYSIS_LOOKBACK_1M, PLOT_WINDOW_MINUTES + 5))
        if len(series) < PLOT_WINDOW_MINUTES:
            log.warning("Not enough 1m candles for %s", sym)
            continue

        df_all = _df_from_candles(series)
        if df_all.empty or len(df_all) < 60:
            continue

        plot_candles = series[-PLOT_WINDOW_MINUTES:]
        df_plot = _df_from_candles(plot_candles)
        if df_plot.empty:
            continue

        ss = structure_summary(df_all)

        close = float(df_plot["Close"].iloc[-1])
        prev = float(df_plot["Close"].iloc[-2]) if len(df_plot) >= 2 else close
        ret = (close - prev) / prev * 100.0 if prev else 0.0

        title = f"{LABELS.get(sym, sym)} — 1m candles (last {PLOT_WINDOW_MINUTES} mins)"
        footer = f"Updated {now_utc} • Close {_fmt_price(sym, close)} ({_fmt_pct(ret)})"

        png = make_candlestick_png(
            CandleSeries(sym, plot_candles),
            title=f"{title}\n{footer}",
            footer=footer,
            dpi=CHART_DPI
        )

        bos = ss.bos or "-"
        choch = ss.choch or "-"
        ph = f"{ss.last_pivot_high:.4f}" if ss.last_pivot_high else "-"
        pl = f"{ss.last_pivot_low:.4f}" if ss.last_pivot_low else "-"
        atr_info = f"{ss.atr:.6f}".rstrip("0").rstrip(".") if ss.atr is not None else "-"
        regime = ss.atr_regime

        caption = (
            f"*{LABELS.get(sym, sym)}*\n"
            f"• *Close:* `{_fmt_price(sym, close)}` (`{_fmt_pct(ret)}`)\n"
            f"• *Structure:* trend `{ss.trend}` | BOS `{bos}` | CHOCH `{choch}`\n"
            f"• *Pivots:* PH `{ph}` | PL `{pl}`\n"
            f"• *Volatility:* ATR `{atr_info}` | regime `{regime}`\n"
            f"• *Time:* `{now_utc}`\n"
            "_Lossless chart for zoom clarity._"
        )

        try:
            fname = f"{sym}_1m_last{PLOT_WINDOW_MINUTES}.png".replace("/", "_")
            await _send_png_document(bot, fname, png, caption)
        except Exception as e:
            log.exception("Telegram chart failed sym=%s err=%s", sym, e)
        finally:
            del png
            gc.collect()


async def job_orderbook_alerts() -> None:
    """
    Higher-signal alerting:
      - rolling anomaly scoring (z-scores)
      - persistence confirmation
      - cooldown + novelty gating
      - focus on events (vacuum / stress / shock / wall / big add/pull)
    """
    global _last_ob, _last_ob_alert_ts, _last_ob_signature, _ob_hist

    if not (HAS_TELEGRAM and ORDERBOOK_ALERTS):
        return

    now = datetime.now(timezone.utc)
    now_utc = now.strftime("%Y-%m-%d %H:%M UTC")
    now_ts = time.time()

    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/ob"}) as client:
        try:
            depth = await fetch_binance_depth(client, symbol="XRPUSDT", limit=1000)
            sig = analyze_depth(
                depth,
                symbol="XRPUSDT",
                depth_pct_band=0.0025,
                wall_usd_threshold=OB_WALL_USD_THRESHOLD,
                prev=_last_ob
            )
        except Exception as e:
            log.exception("Orderbook fetch/analyze failed: %s", e)
            return

    if not sig:
        return

    # collect rolling history for adaptive stats
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
    d_bids = [x["d_bid"] for x in _ob_hist]
    d_asks = [x["d_ask"] for x in _ob_hist]

    z_spread = _zscore(sig.spread_bps, spreads)
    z_imb = _zscore(sig.imbalance, imbs)
    z_bid_depth = _zscore(sig.bid_depth_usd, bids)
    z_ask_depth = _zscore(sig.ask_depth_usd, asks)

    # event detectors (interesting statistics)
    spread_stress = (sig.spread_bps >= OB_SPREAD_BPS_THRESHOLD) or (z_spread >= 3.0)
    imbalance_shock = (abs(sig.imbalance) >= OB_IMBALANCE_THRESHOLD) or (abs(z_imb) >= 3.0)

    # liquidity vacuum: depth unusually low near mid
    depth_collapse = (z_bid_depth <= -2.5) or (z_ask_depth <= -2.5)

    # adaptive delta thresholds (use both absolute + statistical)
    d_bid_std = _std(d_bids)
    d_ask_std = _std(d_asks)

    pull = (sig.delta_bid_depth_usd < -max(OB_DEPTH_DELTA_USD, 3.0 * d_bid_std)) or (
        sig.delta_ask_depth_usd < -max(OB_DEPTH_DELTA_USD, 3.0 * d_ask_std)
    )
    add = (sig.delta_bid_depth_usd > max(OB_DEPTH_DELTA_USD, 3.0 * d_bid_std)) or (
        sig.delta_ask_depth_usd > max(OB_DEPTH_DELTA_USD, 3.0 * d_ask_std)
    )

    wall = sig.top_wall_side != "NONE"

    # persistence confirmation buffers (kept as function attributes)
    if not hasattr(job_orderbook_alerts, "_recent_flags"):
        job_orderbook_alerts._recent_flags = {
            "spread": deque(maxlen=OB_CONFIRM_WINDOW),
            "imb": deque(maxlen=OB_CONFIRM_WINDOW),
            "vac": deque(maxlen=OB_CONFIRM_WINDOW),
            "pull": deque(maxlen=OB_CONFIRM_WINDOW),
            "add": deque(maxlen=OB_CONFIRM_WINDOW),
            "wall": deque(maxlen=OB_CONFIRM_WINDOW),
        }
    rf = job_orderbook_alerts._recent_flags
    rf["spread"].append(spread_stress)
    rf["imb"].append(imbalance_shock)
    rf["vac"].append(depth_collapse)
    rf["pull"].append(pull)
    rf["add"].append(add)
    rf["wall"].append(wall)

    confirmed = (
        _confirm(rf["spread"], OB_CONFIRM_HITS, OB_CONFIRM_WINDOW) or
        _confirm(rf["imb"], OB_CONFIRM_HITS, OB_CONFIRM_WINDOW) or
        _confirm(rf["vac"], OB_CONFIRM_HITS, OB_CONFIRM_WINDOW) or
        _confirm(rf["pull"], OB_CONFIRM_HITS, OB_CONFIRM_WINDOW) or
        _confirm(rf["add"], OB_CONFIRM_HITS, OB_CONFIRM_WINDOW) or
        _confirm(rf["wall"], OB_CONFIRM_HITS, OB_CONFIRM_WINDOW)
    )

    # major bypass: rare combos
    major = (depth_collapse and spread_stress) or (pull and spread_stress) or (abs(z_imb) >= 4.0)

    if not confirmed and not major:
        _last_ob = sig
        return

    # cooldown + novelty gating
    signature = (
        round(sig.mid, 4),
        round(sig.spread_bps, 1),
        round(sig.imbalance, 2),
        sig.top_wall_side,
        int(sig.top_wall_usd / 50000) if sig.top_wall_usd else 0,
        int(sig.delta_bid_depth_usd / 50000),
        int(sig.delta_ask_depth_usd / 50000),
    )

    if not major:
        if (now_ts - _last_ob_alert_ts) < OB_COOLDOWN_SEC:
            _last_ob = sig
            return
        if signature == _last_ob_signature:
            _last_ob = sig
            return

    # DB-level dedupe (kept)
    bucket = (
        f"{int(sig.mid*10000)}|{int(sig.spread_bps)}|{round(sig.imbalance,2)}|"
        f"{sig.top_wall_side}|{int(sig.top_wall_usd/10000)}|"
        f"{int(sig.delta_bid_depth_usd/10000)}|{int(sig.delta_ask_depth_usd/10000)}"
    )
    signal_id = f"ob:{bucket}"

    if not insert_orderbook_signal(
        signal_id,
        sig.symbol,
        sig.mid,
        sig.spread_bps,
        sig.bid_depth_usd,
        sig.ask_depth_usd,
        sig.imbalance,
        sig.top_wall_side,
        sig.top_wall_usd,
        sig.top_wall_price,
        "binance_depth",
        now
    ):
        _last_ob = sig
        return

    movements = []
    if major:
        movements.append("🔥 Major")
    if depth_collapse:
        movements.append("🕳️ Liquidity vacuum")
    if pull:
        movements.append("⚠️ Liquidity pull")
    if add:
        movements.append("✅ Liquidity add")
    if imbalance_shock:
        movements.append("📐 Imbalance shock")
    if spread_stress:
        movements.append("📏 Spread stress")
    if wall:
        movements.append("🧱 Wall")

    movement_str = ", ".join(movements) if movements else "Signal"

    msg = (
        f"🏦 *Order Book Alert (XRPUSDT)* — {movement_str}\n"
        f"• *Time:* `{now_utc}`\n"
        f"• *Mid:* `{sig.mid:.4f}` | *Spread:* `{sig.spread_bps:.2f} bps` (z `{z_spread:+.1f}`)\n"
        f"• *Depth (±25bps):* Bid `{_fmt_notional(sig.bid_depth_usd)}` (z `{z_bid_depth:+.1f}`, Δ `{_fmt_notional(sig.delta_bid_depth_usd)}`) "
        f"vs Ask `{_fmt_notional(sig.ask_depth_usd)}` (z `{z_ask_depth:+.1f}`, Δ `{_fmt_notional(sig.delta_ask_depth_usd)}`)\n"
        f"• *Imbalance:* `{sig.imbalance:+.2f}` (z `{z_imb:+.1f}`)\n"
    )
    if wall:
        msg += f"• *Wall:* `{sig.top_wall_side}` `{_fmt_notional(sig.top_wall_usd)}` @ `{sig.top_wall_price:.4f}`\n"
    msg += ""

    try:
        bot = await _bot()
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
        _last_ob_alert_ts = now_ts
        _last_ob_signature = signature
    except Exception as e:
        log.exception("Orderbook telegram failed: %s", e)

    _last_ob = sig


@app.on_event("startup")
async def startup():
    init_db()

    scheduler.add_job(job_fetch_store_1m, "interval", minutes=1, max_instances=1, coalesce=True)
    scheduler.add_job(job_post_1m_last15_charts, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
    scheduler.add_job(job_orderbook_alerts, "interval", seconds=30, max_instances=1, coalesce=True)

    scheduler.start()
    log.info("Scheduler started. HAS_TELEGRAM=%s", HAS_TELEGRAM)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "ok": True})


@app.get("/health")
async def health():
    return {
        "ok": True,
        "telegram": HAS_TELEGRAM,
        "interval": PRIMARY_INTERVAL,
        "plot_window_minutes": PLOT_WINDOW_MINUTES,
        "analysis_lookback_1m": ANALYSIS_LOOKBACK_1M,
        "symbols": [LABELS.get(s, s) for s in SYMBOLS],
        "orderbook": {
            "enabled": ORDERBOOK_ALERTS,
            "cooldown_sec": OB_COOLDOWN_SEC,
            "confirm_window": OB_CONFIRM_WINDOW,
            "confirm_hits": OB_CONFIRM_HITS,
            "rolling_window": _OB_WINDOW,
        }
    }
