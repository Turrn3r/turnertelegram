from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


@dataclass
class SeriesData:
    symbol: str
    points: list[dict]  # [{"ts": "...", "price": ...}, ...]


def _to_df(points: list[dict]) -> pd.DataFrame:
    if not points:
        return pd.DataFrame(columns=["ts", "price"])
    df = pd.DataFrame(points)
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts", "price"]).sort_values("ts")
    df["price"] = df["price"].astype(float)
    return df


def ema(series: pd.Series, span: int = 20) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)

    roll_up = up.ewm(alpha=1 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False).mean()

    rs = roll_up / roll_down.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.bfill().clip(0, 100)


def make_four_panel_chart_png(series_list: list[SeriesData]) -> bytes:
    plt.style.use("dark_background")

    n = len(series_list)
    if n == 0:
        raise ValueError("No series provided")

    fig = plt.figure(figsize=(12, 3.2 * n), dpi=160)
    gs = fig.add_gridspec(nrows=2 * n, ncols=1, height_ratios=[3, 1] * n, hspace=0.35)

    for i, s in enumerate(series_list):
        df = _to_df(s.points)
        ax_price = fig.add_subplot(gs[2 * i, 0])
        ax_rsi = fig.add_subplot(gs[2 * i + 1, 0], sharex=ax_price)

        if df.empty:
            ax_price.set_title(f"{s.symbol} (no data yet)")
            ax_price.grid(True, alpha=0.2)
            ax_rsi.grid(True, alpha=0.2)
            continue

        if df.shape[0] < 2:
            ax_price.plot(df["ts"], df["price"], marker="o")
            ax_price.set_title(f"{s.symbol} (collecting…)")
            ax_price.grid(True, alpha=0.2)

            ax_rsi.set_title("RSI14 (need more data)")
            ax_rsi.set_ylim(0, 100)
            ax_rsi.grid(True, alpha=0.2)
            continue

        df["ema20"] = ema(df["price"], span=20)
        df["rsi14"] = rsi(df["price"], period=14)

        ax_price.plot(df["ts"], df["price"], linewidth=1.6)
        ax_price.plot(df["ts"], df["ema20"], linewidth=1.2, linestyle="--")
        ax_price.set_title(f"{s.symbol} | Price + EMA20")
        ax_price.grid(True, alpha=0.2)

        ax_rsi.plot(df["ts"], df["rsi14"], linewidth=1.2)
        ax_rsi.axhline(70, linewidth=0.8, linestyle=":")
        ax_rsi.axhline(30, linewidth=0.8, linestyle=":")
        ax_rsi.set_ylim(0, 100)
        ax_rsi.set_title("RSI14")
        ax_rsi.grid(True, alpha=0.2)

        for label in ax_price.get_xticklabels():
            label.set_visible(False)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    fig.suptitle(f"TurnerTrading Live Charts — {now}", fontsize=14, y=0.995)

    import io
    buf = io.BytesIO()
    fig.tight_layout(rect=[0, 0, 1, 0.985])
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
