from __future__ import annotations
import pandas as pd
from .indicators import ema, atr


def classify_regime(df: pd.DataFrame) -> dict:
    close = df["Close"]
    e20 = ema(close, 20)
    e50 = ema(close, 50)
    e200 = ema(close, 200)
    a14 = atr(df, 14)

    last = float(close.iloc[-1])
    last_a = float(a14.iloc[-1])

    trend_up = float(e20.iloc[-1]) >= float(e50.iloc[-1]) >= float(e200.iloc[-1])
    trend_dn = float(e20.iloc[-1]) <= float(e50.iloc[-1]) <= float(e200.iloc[-1])

    # ATR regime: compare current ATR to rolling median
    med = float(a14.tail(240).median()) if len(a14) >= 240 else float(a14.median())
    vol_regime = "HIGH" if last_a > 1.25 * med else ("LOW" if last_a < 0.85 * med else "NORMAL")

    return {
        "last": last,
        "atr": last_a,
        "ema20": float(e20.iloc[-1]),
        "ema50": float(e50.iloc[-1]),
        "ema200": float(e200.iloc[-1]),
        "trend": "UP" if trend_up else ("DOWN" if trend_dn else "MIXED"),
        "vol_regime": vol_regime,
    }
