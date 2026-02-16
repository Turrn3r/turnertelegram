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
    # Ensure numeric
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Volume" in df.columns:
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return df


def make_candlestick_png(series: CandleSeries, title: str, show_volume: bool = False) -> bytes:
    df = _to_df(series.candles)

    if df.empty:
        # create a minimal png if no data
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(10, 6), dpi=160)
        ax = fig.add_subplot(111)
        ax.set_title(title)
        ax.text(0.5, 0.5, "NO DATA", ha="center", va="center", fontsize=24)
        ax.axis("off")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.35)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    buf = io.BytesIO()
    mpf.plot(
        df,
        type="candle",
        style="yahoo",
        title=title,
        ylabel="Price",
        volume=show_volume and ("Volume" in df.columns),
        mav=(9, 21),
        tight_layout=True,
        savefig=dict(fname=buf, dpi=160, bbox_inches="tight", pad_inches=0.35),
    )
    buf.seek(0)
    return buf.read()
