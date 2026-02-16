diff --git a/app/main.py b/app/main.py
index e7de617911f026e3313c95e19e6c2a58a9de0739..5cdba605da46fbe824c51085ff2c954c5c64dcd9 100644
--- a/app/main.py
+++ b/app/main.py
@@ -1,84 +1,112 @@
 import os
 import logging
 from datetime import datetime, timezone
 
 from fastapi import FastAPI
 from fastapi.responses import HTMLResponse, Response
 from fastapi.templating import Jinja2Templates
 from fastapi.requests import Request
 
 from apscheduler.schedulers.asyncio import AsyncIOScheduler
 
-from .storage import init_db, insert_point, last_n_points, last_point, previous_point, insert_news_item
-from .pricing import fetch_all, SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER, SYMBOL_OIL
+from .storage import (
+    init_db,
+    insert_flow_event,
+    insert_news_item,
+    insert_point,
+    last_n_points,
+    last_point,
+    previous_point,
+)
+from .pricing import (
+    SYMBOL_GOLD,
+    SYMBOL_OIL,
+    SYMBOL_SILVER,
+    SYMBOL_XRP,
+    fetch_all,
+    fetch_large_xrp_trades,
+)
 from .charting import SeriesData, make_telegram_chart_png
 from .news import fetch_news
 
 logging.basicConfig(level=logging.INFO)
 log = logging.getLogger("turnertelegram")
 
 TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
 TELEGRAM_CHAT_ID_RAW = os.getenv("TELEGRAM_CHAT_ID", "")
 POST_TO_TELEGRAM = os.getenv("POST_TO_TELEGRAM", "true").lower() in ("1", "true", "yes", "y")
 
 FETCH_EVERY_MINUTES = int(os.getenv("FETCH_EVERY_MINUTES", "15"))
 HISTORY_POINTS = int(os.getenv("HISTORY_POINTS", "288"))
 
 NEWS_ALERTS = os.getenv("NEWS_ALERTS", "true").lower() in ("1", "true", "yes", "y")
 NEWS_THRESHOLD = float(os.getenv("NEWS_THRESHOLD", "2.5"))
 NEWS_SEND_NEUTRAL = os.getenv("NEWS_SEND_NEUTRAL", "false").lower() in ("1", "true", "yes", "y")
 
+FLOW_ALERTS = os.getenv("FLOW_ALERTS", "true").lower() in ("1", "true", "yes", "y")
+FLOW_NOTIONAL_THRESHOLD_USD = float(os.getenv("FLOW_NOTIONAL_THRESHOLD_USD", "250000"))
+
 SYMBOLS = [SYMBOL_XRP, SYMBOL_GOLD, SYMBOL_SILVER, SYMBOL_OIL]
 
 app = FastAPI()
 templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
 scheduler = AsyncIOScheduler()
 
 def normalize_chat_id(chat_id: str) -> str:
     chat_id = (chat_id or "").strip()
     if chat_id.startswith("https://t.me/") or chat_id.startswith("http://t.me/"):
         chat_id = chat_id.split("t.me/")[-1].strip("/")
     if chat_id.startswith("t.me/"):
         chat_id = chat_id.split("t.me/")[-1].strip("/")
     if chat_id and not chat_id.startswith("@") and not chat_id.lstrip("-").isdigit():
         chat_id = "@" + chat_id
     return chat_id
 
 TELEGRAM_CHAT_ID = normalize_chat_id(TELEGRAM_CHAT_ID_RAW)
 
 def _fmt_price(sym: str, px: float) -> str:
     return f"{px:,.4f}" if sym == SYMBOL_XRP else f"{px:,.2f}"
 
 def _pct_change(curr: float, prev: float) -> float:
     if prev == 0:
         return 0.0
     return (curr - prev) / prev * 100.0
 
 def _emoji(sig: str) -> str:
     return {"BUY": "🟢", "SELL": "🔴", "NEUTRAL": "🟡"}.get(sig, "🟡")
 
+
+def _fmt_notional(value: float) -> str:
+    if value >= 1_000_000_000:
+        return f"${value / 1_000_000_000:.2f}B"
+    if value >= 1_000_000:
+        return f"${value / 1_000_000:.2f}M"
+    if value >= 1_000:
+        return f"${value / 1_000:.1f}K"
+    return f"${value:,.0f}"
+
 async def job_fetch_and_store() -> None:
     quotes = await fetch_all()
     now = datetime.now(timezone.utc)
 
     if not quotes:
         log.warning("Price fetch returned 0 quotes")
         return
 
     for q in quotes:
         insert_point(q.symbol, q.price, q.source, ts=now)
 
     log.info("Stored quotes: %s", ", ".join([f"{q.symbol}={q.price}" for q in quotes]))
 
 async def job_post_channel_update() -> None:
     if not POST_TO_TELEGRAM:
         return
     if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
         log.warning("Telegram not configured (missing token or chat id)")
         return
 
     series = [SeriesData(symbol=sym, points=last_n_points(sym, limit=HISTORY_POINTS)) for sym in SYMBOLS]
 
     try:
         png = make_telegram_chart_png(series)
         log.info("Chart PNG bytes=%d", len(png))
@@ -91,50 +119,102 @@ async def job_post_channel_update() -> None:
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
             lines.append(f"{sym}: {_fmt_price(sym, curr)} ({arrow} {pct:+.2f}%)")
         else:
             lines.append(f"{sym}: {_fmt_price(sym, curr)} (collecting…)")
 
     caption = "📊 TurnerTrading — Update\n" + "\n".join(lines) + "\n#XRP #GOLD #SILVER #OIL"
 
     from telegram import Bot
     bot = Bot(token=TELEGRAM_BOT_TOKEN)
 
     try:
         await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=png, caption=caption)
         log.info("Posted chart update to Telegram: %s", TELEGRAM_CHAT_ID)
     except Exception as e:
         log.exception("Telegram chart post failed: %s", e)
 
+
+
+async def job_flow_alerts() -> None:
+    if not (POST_TO_TELEGRAM and FLOW_ALERTS):
+        return
+    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
+        return
+
+    from telegram import Bot
+
+    bot = Bot(token=TELEGRAM_BOT_TOKEN)
+
+    import httpx
+
+    async with httpx.AsyncClient(headers={"User-Agent": "turnertelegram/1.0"}) as client:
+        events = await fetch_large_xrp_trades(client, min_notional_usd=FLOW_NOTIONAL_THRESHOLD_USD)
+
+    sent = 0
+    for ev in events:
+        inserted = insert_flow_event(
+            event_id=ev.event_id,
+            symbol=ev.symbol,
+            side=ev.side,
+            price=ev.price,
+            quantity=ev.quantity,
+            notional_usd=ev.notional_usd,
+            source=ev.source,
+            ts=ev.ts_utc,
+        )
+        if not inserted:
+            continue
+
+        direction = "🟢 BUY pressure" if ev.side == "BUY" else "🔴 SELL pressure"
+        msg = (
+            "🐋 *High-Interest XRP Flow Alert*\n"
+            f"*Signal:* {direction}\n"
+            f"*Notional:* `{_fmt_notional(ev.notional_usd)}`\n"
+            f"*Price:* `{_fmt_price(ev.symbol, ev.price)}`\n"
+            f"*Size:* `{ev.quantity:,.0f} XRP`\n"
+            "*Source:* `Binance aggregated tape`\n\n"
+            "_This is a large-flow proxy for institutional/hedge-fund interest, not identified account-level data._"
+        )
+
+        try:
+            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
+            sent += 1
+        except Exception as e:
+            log.exception("Telegram flow post failed: %s", e)
+
+    if events:
+        log.info("Flow job complete. scanned=%d sent=%d threshold=%s", len(events), sent, FLOW_NOTIONAL_THRESHOLD_USD)
+
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
 
     sent = 0
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
@@ -148,47 +228,48 @@ async def job_news_alerts() -> None:
             f"{_emoji(it.signal)} *NEWS ALERT* ({it.signal} bias)\n"
             f"*Tags:* `{it.tags}`\n"
             f"*Source:* `{it.source}`\n"
             f"*Headline:* {it.title}\n"
         )
         if it.link:
             msg += f"{it.link}\n"
         msg += "\n_Disclaimer: automated headline scoring; not financial advice._"
 
         try:
             await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown")
             sent += 1
         except Exception as e:
             log.exception("Telegram news post failed: %s", e)
 
     log.info("News job complete. Items=%d Sent=%d", len(items), sent)
 
 @app.on_event("startup")
 async def startup():
     init_db()
     await job_fetch_and_store()
 
     scheduler.add_job(job_fetch_and_store, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
     scheduler.add_job(job_post_channel_update, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
     scheduler.add_job(job_news_alerts, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
+    scheduler.add_job(job_flow_alerts, "interval", minutes=FETCH_EVERY_MINUTES, max_instances=1, coalesce=True)
 
     scheduler.start()
     log.info("Scheduler started interval=%d chat_id=%s", FETCH_EVERY_MINUTES, TELEGRAM_CHAT_ID)
 
 @app.get("/", response_class=HTMLResponse)
 async def index(request: Request):
     return templates.TemplateResponse("index.html", {"request": request, "symbols": SYMBOLS})
 
 @app.get("/api/series")
 async def api_series(limit: int = 500):
     limit = max(10, min(limit, 2000))
     return {sym: last_n_points(sym, limit=limit) for sym in SYMBOLS}
 
 @app.get("/debug/chart.png")
 async def debug_chart_png():
     series = [SeriesData(symbol=sym, points=last_n_points(sym, limit=HISTORY_POINTS)) for sym in SYMBOLS]
     png = make_telegram_chart_png(series)
     return Response(content=png, media_type="image/png")
 
 @app.get("/health")
 async def health():
     return {"ok": True}
