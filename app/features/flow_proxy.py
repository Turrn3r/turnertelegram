from __future__ import annotations
import pandas as pd


def flow_proxy(df: pd.DataFrame) -> dict:
    """
    Institutional-interest proxy when no true gold orderbook is available.
    Detects:
      - range expansion
      - volatility regime shifts
      - impulsive move vs baseline
    """
    if df.empty or len(df) < 120:
        return {"flow_score": 0.0, "notes": ["insufficient history"]}

    close = df["Close"]
    ret = close.pct_change().fillna(0)
    # z-score of last return vs rolling baseline
    base = ret.tail(240) if len(ret) >= 240 else ret
    mu = float(base.mean())
    sd = float(base.std(ddof=1)) if float(base.std(ddof=1)) > 1e-12 else 1e-12
    z = float((ret.iloc[-1] - mu) / sd)

    # range expansion
    rng = (df["High"] - df["Low"]).tail(240)
    rmu = float(rng.mean())
    rsd = float(rng.std(ddof=1)) if float(rng.std(ddof=1)) > 1e-12 else 1e-12
    z_rng = float(((df["High"].iloc[-1] - df["Low"].iloc[-1]) - rmu) / rsd)

    score = 0.0
    notes = []
    if abs(z) >= 2.0:
        score += 0.35
        notes.append(f"impulse z={z:+.1f}")
    if z_rng >= 2.0:
        score += 0.35
        notes.append(f"range expansion z={z_rng:+.1f}")

    score = min(1.0, score)
    return {"flow_score": score, "notes": notes}
