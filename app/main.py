import os
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .storage import (
    init_db, insert_point, last_n_points, last_point, previous_point,
    insert_news_item
)
from .pricing import fetch_all, SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER, SYMBOL_OIL
from .charting import SeriesData, make_four_panel_chart_png
from .news import fetch_news

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # e.g. "@turnertrading"
POST_TO_TELEGRAM = os.getenv("POST_TO_TELEGRAM", "true").lower() in ("1", "true", "yes", "y")

FETCH_EVERY_MINUTES = int(os.getenv("FETCH_EVERY_MINUTES", "15"))
HISTORY_POINTS = int(os.getenv("HISTORY_POINTS", "288"))  # ~3 days at 15-min intervals

# News alerts
NEWS_ALERTS = os.getenv("NEWS_ALERTS", "true").lower() in ("1", "true", "yes", "y")
NEWS_THRESHOLD = float(os.getenv("NEWS_THRESHOLD", "2.0"))  # higher = fewer alerts
NEWS_SEND_NEUTRAL = os.getenv("NEWS_SEND_NEUTRAL", "false").lower() in ("1", "true", "yes", "y")

SYMBOLS = [SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER, SYMBOL_OIL]

app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
scheduler = AsyncIOScheduler()


def _fmt_price(sym: str, px: float) -> str:
    if sym == SYMBOL_XRP:
        return f"{px:,.4f}"
    return f"{px:,.2f}"


def _pct_change(curr: float, prev: float) -> float:
    if prev == 0:
        return 0.0
    return (curr - prev) / prev * 100.0


def _emoji(sig: str) -> str:
    return {"BUY": "🟢", "SELL": "🔴", "NEUTRAL": "🟡"}.get(sig, "🟡")


async def job_fetch_and_store() -> None:
    quotes = await fetch_all()
    now = datetime.now(timezone.utc)
    for q in quotes:
        insert_point(q.symbol, q.price, q.source, ts=now)


async def job_post_channel_update() -> None:
    if not POST_TO_TELEGRAM:
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    series = []
    for sym in SYMBOLS:
        pts = last_n_points(sym, limit=HISTORY_POINTS)
        series.append(SeriesData(symbol=sym, points=pts))

    try:
        png = make_four_panel_chart_png(series)
    except Exception:
        return

    lines = []
    for sym in SYMBOLS:
        lp = last_point(sym)
        pp = previous_point(sym)
        if not lp:
            lines.append(f"{sym}: n/a")
            continue
        curr = float(lp["price"])
        if pp:
            prev = float(pp["price"])
            pct = _pct_change(curr, prev)
            arrow = "🟢" if pct >= 0 else "🔴"
            lines.append(f"{sym}: {_fmt_price(sym, curr)}  ({arrow} {pct:+.2f}%)")
        else:
            lines.append(f"{sym}: {_fmt_price(sym, curr)}")

    caption = "📊 TurnerTrading — 15m Update\n" + "\n".join(lines) + "\n#XRP #GOLD #SILVER #OIL"

    from telegram import Bot
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=png, caption=caption)


async def job_news_alerts() -> None:
    if not (POST_TO_TELEGRAM and NEWS_ALERTS):
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    items = fetch_news(threshold=NEWS_THRESHOLD)
    if not items:
        return

    from telegram import Bot
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    # Insert + send only *new* items
    for it in items:
        inserted = insert_news_item(
            guid=it.guid,
            source=it.source,
            title=it.title,
            link=it.link,
            summary=it.summary,
            published=it.published,
            tags=it.tags,
            score=it.score,
            signal=it.signal,
        )
        if not inserted:
            continue

        if it.signal == "NEUTRAL" and not NEWS_SEND_NEUTRAL:
            continue

        # Short, readable message. Not financial advice.
        msg = (
            f"{_emoji(it.signal)} *NEWS ALERT* ({it.signal} bias)\n"
            f"*Tags:* `{it.tags}`\n"
            f"*Source:* `{it.source}`\n"
            f"*Headline:* {it.title}\n"
        )
        if it.link:
            msg += f"{it.link}\n"
        msg += "\n_Disclaimer: automated headline scoring; not financial advice._"

        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")


@app.on_event("startup")
async def startup():
    init_db()

    # Prime prices at boot
    await job_fetch_and_store()

    # Schedule every 15 minutes
    scheduler.add_job(job_fetch_and_store, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
    scheduler.add_job(job_post_channel_update, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)

    # News alerts (also every 15 minutes by default)
    scheduler.add_job(job_news_alerts, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)

    scheduler.start()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "symbols": SYMBOLS})


@app.get("/api/series")
async def api_series(limit: int = 500):
    limit = max(10, min(limit, 2000))
    return {sym: last_n_points(sym, limit=limit) for sym in SYMBOLS}


@app.get("/health")
async def health():
    return {"ok": True}
