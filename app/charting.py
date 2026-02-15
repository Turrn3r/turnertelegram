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

    fig = plt.figure(figsize=(12, 7), dpi=170)
    gs = fig.add_gridspec(2, 2, hspace=0.35, wspace=0.20)

    layout = {
        "XRPUSD": (0, 0),
        "XAUUSD": (0, 1),
        "XAGUSD": (1, 0),
        "CL.F": (1, 1),
    }

    by_sym = {s.symbol: s for s in series_list}

    for sym, (r, c) in layout.items():
        ax = fig.add_subplot(gs[r, c])
        df = _to_df(by_sym.get(sym, SeriesData(sym, [])).points)

        if df.empty:
            ax.set_title(f"{sym} (no data yet)")
            ax.grid(True, alpha=0.2)
            continue

        # Always draw with markers so flat lines are visible
        ax.plot(df["ts"], df["price"], marker="o", linewidth=1.6)
        ax.set_title(sym)
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
