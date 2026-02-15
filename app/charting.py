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
    plt.style.use("dark_background")

    fig, axes = plt.subplots(2, 2, figsize=(12, 7), dpi=170)
    axes = axes.flatten()

    layout = ["XRPUSD", "XAUUSD", "XAGUSD", "CL.F"]
    by_sym = {s.symbol: s for s in series_list}

    for ax, sym in zip(axes, layout):
        df = _to_df(by_sym.get(sym, SeriesData(sym, [])).points)

        if df.empty:
            ax.set_title(f"{sym} (no data yet)")
            ax.grid(True, alpha=0.25)
            continue

        # Markers ensure visibility even if the line is flat
        ax.plot(df["ts"], df["price"], marker="o", linewidth=1.6)
        ax.set_title(sym)
        ax.grid(True, alpha=0.25)

        # Improve readability: rotate x labels a bit
        for label in ax.get_xticklabels():
            label.set_rotation(20)
            label.set_horizontalalignment("right")

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    fig.suptitle(f"TurnerTrading — Charts — {now}", fontsize=14)

    import io
    buf = io.BytesIO()

    # IMPORTANT: do NOT use tight_layout here (it caused the warning & blank-looking images)
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)

    buf.seek(0)
    return buf.read()
