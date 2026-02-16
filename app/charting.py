from __future__ import annotations

import io
from dataclasses import dataclass

import matplotlib
matplotlib.use("Agg")

import pandas as pd
import mplfinance as mpf


@dataclass
class CandleSeries:
    symbol: str
    candles: list[dict]  # [{t,open,high,low,close,volume?}, ...]


def _to_df(candles: list[dict]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

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


def _dark_style() -> mpf.MpfStyle:
    # A crisp dark theme that stays legible in Telegram compression.
    marketcolors = mpf.make_marketcolors(
        up="#2de37a",
        down="#ff4d4d",
        edge="inherit",
        wick="inherit",
        volume="inherit",
    )
    return mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=marketcolors,
        facecolor="#0b0f14",
        figcolor="#0b0f14",
        gridcolor="#2a2f3a",
        rc={
            "axes.labelcolor": "#cfd6e6",
            "xtick.color": "#cfd6e6",
            "ytick.color": "#cfd6e6",
            "text.color": "#cfd6e6",
            "axes.edgecolor": "#2a2f3a",
            "axes.grid": True,
            "grid.alpha": 0.20,
            "font.size": 10,
        },
    )


def make_candlestick_png(
    series: CandleSeries,
    title: str,
    subtitle: str,
    show_volume: bool = False,
) -> bytes:
    df = _to_df(series.candles)

    # If no data, still create a usable PNG
    if df.empty:
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(10, 6), dpi=180)
        fig.patch.set_facecolor("#0b0f14")
        ax = fig.add_subplot(111)
        ax.set_facecolor("#0b0f14")
        ax.set_title(title, color="#cfd6e6")
        ax.text(0.5, 0.52, "NO DATA", ha="center", va="center", fontsize=24, color="#cfd6e6")
        ax.text(0.5, 0.44, subtitle, ha="center", va="center", fontsize=11, color="#8ea0bf")
        ax.axis("off")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.35)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    style = _dark_style()

    # Perception upgrades:
    # - EMA(9,21) for trend + pullback structure
    # - Tight layout + legible time axis formatting
    # - High DPI so candles remain sharp in Telegram
    buf = io.BytesIO()
    mpf.plot(
        df,
        type="candle",
        style=style,
        figsize=(11.5, 7.0),
        title=title,
        ylabel="",
        volume=show_volume and ("Volume" in df.columns),
        mav=(9, 21),
        datetime_format="%H:%M",   # SHOW MINUTES (key change)
        xrotation=0,
        tight_layout=True,
        scale_width_adjustment=dict(candle=1.10, volume=0.90),
        update_width_config=dict(
            candle_linewidth=1.0,
            candle_width=0.70,
            volume_linewidth=0.6,
            volume_width=0.70,
        ),
        addplot=[],
        savefig=dict(fname=buf, dpi=220, bbox_inches="tight", pad_inches=0.35),
    )

    # Add subtitle (timeframe + last update) via figure text overlay
    # mplfinance does not expose a direct subtitle; we re-open with matplotlib and add text is costly.
    # Instead: encode subtitle into title line for Telegram clarity.
    # (So main.py should pass title already containing timeframe and subtitle.)
    buf.seek(0)
    return buf.read()
