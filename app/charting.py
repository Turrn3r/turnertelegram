from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import matplotlib.pyplot as plt
import mplfinance as mpf


@dataclass
class SeriesData:
    symbol: str
    points: list[dict]  # [{"ts": "...", "price": ...}, ...]


def _to_line_df(points: list[dict]) -> pd.DataFrame:
    if not points:
        return pd.DataFrame(columns=["ts", "price"])
    df = pd.DataFrame(points)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts", "price"]).sort_values("ts")
    df["price"] = df["price"].astype(float)
    return df


def _to_ohlc_df(candles: list[dict]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    df = pd.DataFrame(candles)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts", "open", "high", "low", "close"]).sort_values("ts")
    df = df.set_index("ts")
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    return df[["Open", "High", "Low", "Close", "Volume"]]


def make_telegram_chart_png(
    series_list: list[SeriesData],
    xrp_candles: list[dict],
) -> bytes:
    plt.style.use("dark_background")

    # Layout: 2x2 grid
    fig = plt.figure(figsize=(12, 7), dpi=170)
    gs = fig.add_gridspec(2, 2, hspace=0.35, wspace=0.20)

    # --- XRP candlesticks (top-left)
    ax_xrp = fig.add_subplot(gs[0, 0])
    ohlc = _to_ohlc_df(xrp_candles)

    if len(ohlc) >= 2:
        mpf.plot(
            ohlc,
            type="candle",
            ax=ax_xrp,
            volume=False,
            style="nightclouds",
            xrotation=0,
            show_nontrading=True,
        )
        ax_xrp.set_title("XRPUSD (15m Candles)")
    else:
        ax_xrp.set_title("XRPUSD (candles collecting…)")

    # --- Other 3 line charts (with markers so flat lines are visible)
    symbols = {s.symbol: s for s in series_list}
    for sym, pos in [("XAUUSD", (0, 1)), ("XAGUSD", (1, 0)), ("CL.F", (1, 1))]:
        ax = fig.add_subplot(gs[pos[0], pos[1]])
        df = _to_line_df(symbols.get(sym, SeriesData(sym, [])).points)

        if df.empty:
            ax.set_title(f"{sym} (no data yet)")
            ax.grid(True, alpha=0.2)
            continue

        # Always draw with markers so even flat lines show clearly
        ax.plot(df["ts"], df["price"], marker="o", linewidth=1.6)
        ax.set_title(f"{sym} (Line)")
        ax.grid(True, alpha=0.2)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    fig.suptitle(f"TurnerTrading — Charts — {now}", fontsize=14, y=0.98)

    import io
    buf = io.BytesIO()
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
