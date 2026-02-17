from __future__ import annotations
import io
from typing import Any
from telegram import Bot


def fmt_price(px: float) -> str:
    return f"{px:,.2f}"


async def send_png(bot: Bot, chat_id: str, filename: str, png: bytes, caption: str) -> None:
    f = io.BytesIO(png)
    f.name = filename
    await bot.send_document(chat_id=chat_id, document=f, caption=caption, parse_mode="Markdown")


def render_trade(decision, label: str) -> str:
    r = decision.risk
    cat = decision.snapshot["catalysts"]
    top_news = cat.get("top_news", [])[:4]
    news_lines = "\n".join([f"• {t[:110]}" for t in top_news]) if top_news else "• (none cached)"

    return (
        f"🎯 *Trade Idea ({label})* — *{decision.direction}*  (confidence `{decision.confidence}/100`)\n"
        f"• *Entry:* `{fmt_price(r.entry_low)}` → `{fmt_price(r.entry_high)}`\n"
        f"• *SL:* `{fmt_price(r.sl)}`\n"
        f"• *TP1:* `{fmt_price(r.tp1)}` | *TP2:* `{fmt_price(r.tp2)}`\n"
        f"• *RR (to TP1):* `{r.rr:.2f}`\n\n"
        f"*Why:*\n" + "\n".join([f"• {x}" for x in decision.reasons[:10]]) +
        f"\n\n*Top news:*\n{news_lines}\n\n"
        "_Not financial advice. Use your own risk controls._"
    )
