from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import matplotlib
matplotlib.use("Agg")

import pandas as pd
import numpy as np
import mplfinance as mpf


@dataclass
class CandleSeries:
    symbol: str
    candles: list[dict]  # [{t,open,high,low,close,volume?}, ...]


def _to_df(candles: list[dict]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles)
    df["t"] = pd.to_datetime(df["t"], utc=True, errors="coerce")
    df = df.dropna(subset=["t", "open", "high", "low", "close"]).sort_values("t")
    df = df.set_index("t")

    df = df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )

    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Volume" in df.columns:
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / (avg_loss.replace(0, np.nan))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def _theme_from_env():
    # Defaults are a “Turrner-like” dark fintech palette.
    bg = (matplotlib.rcParams.get("figure.facecolor") or "#0b0f14")
    face = "#0b0f14"
    grid = "#243042"
    text = "#cfd6e6"

    up = "#2de37a"
    down = "#ff4d4d"
    accent = "#8ea0ff"

    # Optional overrides to match turrner.com exactly
    face = os.getenv("BRAND_BG", face)
    grid = os.getenv("BRAND_GRID", grid)
    text = os.getenv("BRAND_TEXT", text)
    up = os.getenv("BRAND_UP", up)
    down = os.getenv("BRAND_DOWN", down)
    accent = os.getenv("BRAND_ACCENT", accent)

    marketcolors = mpf.make_marketcolors(
        up=up,
        down=down,
        edge="inherit",
        wick="inherit",
        volume="inherit",
    )

    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=marketcolors,
        facecolor=face,
        figcolor=face,
        gridcolor=grid,
        rc={
            "axes.labelcolor": text,
            "xtick.color": text,
            "ytick.color": text,
            "text.color": text,
            "axes.edgecolor": grid,
            "axes.grid": True,
            "grid.alpha": 0.22,
            "font.size": 11,
        },
    )
    return style, face, grid, text, accent


def make_candlestick_png(
    series: CandleSeries,
    title: str,
    subtitle: str,
    show_volume: bool = False,
    dpi: int = 360,
) -> bytes:
    df = _to_df(series.candles)
    style, face, grid, text, accent = _theme_from_env()

    if df.empty or len(df) < 5:
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(12, 7), dpi=dpi)
        fig.patch.set_facecolor(face)
        ax = fig.add_subplot(111)
        ax.set_facecolor(face)
        ax.set_title(title, color=text, fontsize=16, fontweight="bold")
        ax.text(0.5, 0.52, "NO DATA", ha="center", va="center", fontsize=28, color=text)
        ax.text(0.5, 0.44, subtitle, ha="center", va="center", fontsize=12, color=accent)
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.35)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    # Indicators: EMA + RSI for pattern recognition
    close = df["Close"]
    rsi = _rsi(close, 14)
    apds = [
        mpf.make_addplot(rsi, panel=1, color=accent, width=1.2, ylabel="RSI"),
    ]

    # RSI guide lines
    # mplfinance doesn’t do horizontal lines in addplot cleanly; we’ll keep it minimal.

    fig, axes = mpf.plot(
        df,
        type="candle",
        style=style,
        figsize=(13.5, 8.0),
        returnfig=True,
        volume=show_volume and ("Volume" in df.columns),
        mav=(9, 21),
        panel_ratios=(3, 1),
        addplot=apds,
        datetime_format="%H:%M",  # actual minute stamps
        xrotation=0,
        tight_layout=True,
        scale_padding={"left": 0.6, "right": 0.8, "top": 0.8, "bottom": 0.8},
        update_width_config=dict(
            candle_linewidth=1.1,
            candle_width=0.70,
            volume_linewidth=0.6,
            volume_width=0.70,
        ),
    )

    fig.suptitle(title, fontsize=16, fontweight="bold", y=0.98, color=text)
    fig.text(0.01, 0.01, subtitle, color=accent, fontsize=11)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.30)
    matplotlib.pyplot.close(fig)
    buf.seek(0)
    return buf.read()
