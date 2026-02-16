from __future__ import annotations

import io
import os
from dataclasses import dataclass

import matplotlib
matplotlib.use("Agg")

import pandas as pd
import mplfinance as mpf

from .analytics import rsi as calc_rsi, atr as calc_atr, pivots as calc_pivots


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

    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Volume" in df.columns:
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df


def _brand(key: str, default: str) -> str:
    return (os.getenv(key, "") or default).strip()


def _dark_style() -> mpf.MpfStyle:
    face = _brand("BRAND_BG", "#0b0f14")
    grid = _brand("BRAND_GRID", "#243042")
    text = _brand("BRAND_TEXT", "#cfd6e6")
    up = _brand("BRAND_UP", "#2de37a")
    down = _brand("BRAND_DOWN", "#ff4d4d")

    marketcolors = mpf.make_marketcolors(up=up, down=down, edge="inherit", wick="inherit", volume="inherit")
    return mpf.make_mpf_style(
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


def make_candlestick_png(series: CandleSeries, title: str, footer: str, dpi: int = 420) -> bytes:
    df = _to_df(series.candles)
    style = _dark_style()
    accent = _brand("BRAND_ACCENT", "#8ea0ff")

    if df.empty or len(df) < 60:
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(13, 8), dpi=dpi)
        fig.patch.set_facecolor(_brand("BRAND_BG", "#0b0f14"))
        ax = fig.add_subplot(111)
        ax.set_title(title, color=_brand("BRAND_TEXT", "#cfd6e6"), fontsize=16, fontweight="bold")
        ax.text(0.5, 0.5, "NO DATA", ha="center", va="center", fontsize=28, color=_brand("BRAND_TEXT", "#cfd6e6"))
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.35)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    # Indicators
    r = calc_rsi(df["Close"], 14)
    a = calc_atr(df, 14)

    # Pivot markers
    ph, pl = calc_pivots(df, 3, 3)
    ph_y = df["High"].where(ph, other=float("nan"))
    pl_y = df["Low"].where(pl, other=float("nan"))

    apds = [
        mpf.make_addplot(r, panel=1, color=accent, width=1.2, ylabel="RSI"),
        mpf.make_addplot(a, panel=2, color=accent, width=1.2, ylabel="ATR"),
        mpf.make_addplot(ph_y, type="scatter", markersize=35, marker="^", color=accent),
        mpf.make_addplot(pl_y, type="scatter", markersize=35, marker="v", color=accent),
    ]

    fig, _axes = mpf.plot(
        df,
        type="candle",
        style=style,
        figsize=(13.8, 8.6),
        returnfig=True,
        mav=(9, 21),
        panel_ratios=(3, 1, 1),
        addplot=apds,
        datetime_format="%H:%M",  # minute time axis
        xrotation=0,
        tight_layout=True,
        update_width_config=dict(candle_linewidth=1.1, candle_width=0.70),
    )

    fig.suptitle(title, fontsize=16, fontweight="bold", y=0.985, color=_brand("BRAND_TEXT", "#cfd6e6"))
    fig.text(0.01, 0.01, footer, color=accent, fontsize=11)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.30)
    matplotlib.pyplot.close(fig)
    buf.seek(0)
    return buf.read()
