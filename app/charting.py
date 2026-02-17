# app/charting.py
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import List, Dict, Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter, date2num
import pandas as pd


@dataclass
class CandleSeries:
    symbol: str
    candles: List[Dict[str, Any]]  # {t, open, high, low, close}


def _to_df(candles: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["t"] = pd.to_datetime(df["t"], utc=True, errors="coerce")
    df = df.dropna(subset=["t", "open", "high", "low", "close"]).sort_values("t")
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


def make_candlestick_png(series: CandleSeries, title: str, footer: str = "", dpi: int = 260) -> bytes:
    df = _to_df(series.candles)
    if df.empty:
        return b""

    x = date2num(df["t"].dt.to_pydatetime())
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()

    bg = "#0b0f1a"
    fg = "#e6edf3"
    grid = "#1f2a44"
    up_color = "#2ee59d"
    down_color = "#ff4d6d"

    fig = plt.figure(figsize=(12, 6), dpi=dpi, facecolor=bg)
    ax = fig.add_subplot(111, facecolor=bg)

    ax.grid(True, color=grid, alpha=0.55, linewidth=0.6)
    ax.tick_params(colors=fg, labelsize=10)
    for spine in ax.spines.values():
        spine.set_color(grid)

    ax.set_title(title, color=fg, fontsize=14, pad=14, fontweight="bold")
    if footer:
        ax.text(0.01, 0.01, footer, transform=ax.transAxes, color=fg, fontsize=9, alpha=0.9, va="bottom")

    w = (x[1] - x[0]) * 0.7 if len(x) >= 2 else 0.0005

    for i in range(len(x)):
        color = up_color if c[i] >= o[i] else down_color
        ax.vlines(x[i], l[i], h[i], color=color, linewidth=1.0, alpha=0.95)
        body_low = min(o[i], c[i])
        body_high = max(o[i], c[i])
        height = max(body_high - body_low, 1e-12)
        ax.add_patch(plt.Rectangle((x[i] - w/2, body_low), w, height,
                                   facecolor=color, edgecolor=color, linewidth=0.8))

    ax.xaxis.set_major_formatter(DateFormatter("%H:%M"))
    ax.set_xlabel("Time (UTC)", color=fg, labelpad=8)
    ax.set_ylabel("Price", color=fg, labelpad=8)
    ax.margins(x=0.02)

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=bg, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
