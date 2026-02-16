from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
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

def make_telegram_chart_png(series_list: list[SeriesData]) -> bytes:
    # Bright background to avoid “blank” looking images in Telegram
    fig, axes = plt.subplots(2, 2, figsize=(12, 7), dpi=180)
    fig.patch.set_facecolor("white")

    axes = axes.flatten()

    # Order matches your instruments
    layout = ["XRPUSD", "GC.F", "SI.F", "CL.F"]
    by_sym = {s.symbol: s for s in series_list}

    for ax, sym in zip(axes, layout):
        ax.set_facecolor("white")

        df = _to_df(by_sym.get(sym, SeriesData(sym, [])).points)
        if df.empty:
            ax.set_title(f"{sym} (no data yet)")
            ax.grid(True, alpha=0.25)
            continue

        # Thick line + large markers so even flat lines show clearly
        ax.plot(df["ts"], df["price"], marker="o", markersize=5, linewidth=2.5)
        ax.set_title(sym)
        ax.grid(True, alpha=0.25)

        # Make x labels readable
        for label in ax.get_xticklabels():
            label.set_rotation(20)
            label.set_horizontalalignment("right")

        # Add small y padding so flat series isn't visually “invisible”
        ymin = df["price"].min()
        ymax = df["price"].max()
        if ymin == ymax:
            pad = max(0.001 * ymin, 0.01)
            ax.set_ylim(ymin - pad, ymax + pad)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    fig.suptitle(f"TurnerTrading — Charts — {now}", fontsize=14)

    import io
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.35)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
