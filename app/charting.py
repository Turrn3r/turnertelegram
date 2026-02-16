from __future__ import annotations

# IMPORTANT: Force headless backend for Fly/Docker
import matplotlib
matplotlib.use("Agg")  # must be before importing pyplot

from dataclasses import dataclass
from datetime import datetime
import io

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
    # Parse timestamps robustly
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts", "price"]).sort_values("ts")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["price"])
    return df


def _fmt(sym: str, px: float) -> str:
    return f"{px:,.4f}" if sym == "XRPUSD" else f"{px:,.2f}"


def make_telegram_chart_png(series_list: list[SeriesData]) -> bytes:
    # Bright background so Telegram previews don’t look “blank”
    fig, axes = plt.subplots(2, 2, figsize=(12, 7), dpi=180)
    fig.patch.set_facecolor("white")
    axes = axes.flatten()

    # Match the symbols you're storing
    layout = ["XRPUSD", "GC.F", "SI.F", "CL.F"]
    by_sym = {s.symbol: s for s in series_list}

    for ax, sym in zip(axes, layout):
        ax.set_facecolor("white")
        ax.grid(True, alpha=0.25)
        ax.set_title(sym)

        df = _to_df(by_sym.get(sym, SeriesData(sym, [])).points)

        if df.empty:
            ax.text(0.5, 0.5, "NO DATA", ha="center", va="center", fontsize=18)
            ax.set_xticks([])
            ax.set_yticks([])
            continue

        if len(df) == 1:
            px = float(df["price"].iloc[0])
            ax.scatter(df["ts"], df["price"], s=120)
            ax.text(
                0.5, 0.5, _fmt(sym, px),
                transform=ax.transAxes,
                ha="center", va="center",
                fontsize=24, fontweight="bold",
            )
            ax.set_xticks([])
            continue

        # Always draw thick + markers so flat series still shows
        ax.plot(df["ts"], df["price"], marker="o", markersize=5, linewidth=2.6)

        ymin = float(df["price"].min())
        ymax = float(df["price"].max())
        if ymin == ymax:
            pad = max(0.001 * ymin, 0.01)
            ax.set_ylim(ymin - pad, ymax + pad)
        else:
            # small padding for visibility
            pad = (ymax - ymin) * 0.08
            ax.set_ylim(ymin - pad, ymax + pad)

        for label in ax.get_xticklabels():
            label.set_rotation(20)
            label.set_horizontalalignment("right")

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    fig.suptitle(f"TurnerTrading — Charts — {now}", fontsize=14)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.35)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
