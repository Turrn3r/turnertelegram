from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class StructureSummary:
    last_pivot_high: float | None
    last_pivot_low: float | None
    bos: str | None       # "BULL" / "BEAR" / None
    choch: str | None     # "BULL" / "BEAR" / None
    trend: str            # "UP" / "DOWN" / "RANGE"
    atr: float | None
    atr_pctile: float | None
    atr_regime: str       # "LOW" / "NORMAL" / "HIGH"


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)

    tr1 = (high - low).abs()
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> tuple[pd.Series, pd.Series]:
    """
    Pivot high at i if High[i] is max over [i-left .. i+right].
    Pivot low at i if Low[i] is min over [i-left .. i+right].
    """
    h = df["High"]
    l = df["Low"]
    ph = pd.Series(False, index=df.index)
    pl = pd.Series(False, index=df.index)

    for i in range(left, len(df) - right):
        window_h = h.iloc[i - left : i + right + 1]
        window_l = l.iloc[i - left : i + right + 1]
        if h.iloc[i] == window_h.max():
            ph.iloc[i] = True
        if l.iloc[i] == window_l.min():
            pl.iloc[i] = True
    return ph, pl


def structure_summary(df: pd.DataFrame) -> StructureSummary:
    if df is None or df.empty or len(df) < 50:
        return StructureSummary(None, None, None, None, "RANGE", None, None, "NORMAL")

    close = df["Close"]
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()

    # Trend heuristic
    if ema9.iloc[-1] > ema21.iloc[-1] and close.iloc[-1] > ema21.iloc[-1]:
        trend = "UP"
    elif ema9.iloc[-1] < ema21.iloc[-1] and close.iloc[-1] < ema21.iloc[-1]:
        trend = "DOWN"
    else:
        trend = "RANGE"

    ph, pl = pivots(df, 3, 3)
    pivot_highs = df.loc[ph, "High"]
    pivot_lows = df.loc[pl, "Low"]

    last_ph = float(pivot_highs.iloc[-1]) if len(pivot_highs) else None
    last_pl = float(pivot_lows.iloc[-1]) if len(pivot_lows) else None

    # BOS / CHOCH approximation:
    # - BOS bullish if close breaks last pivot high in an UP trend
    # - BOS bearish if close breaks last pivot low in a DOWN trend
    # - CHOCH if break opposite trend expectation
    bos = None
    choch = None
    last_close = float(close.iloc[-1])

    if last_ph is not None and last_close > last_ph:
        if trend == "UP":
            bos = "BULL"
        elif trend in ("DOWN", "RANGE"):
            choch = "BULL"

    if last_pl is not None and last_close < last_pl:
        if trend == "DOWN":
            bos = "BEAR"
        elif trend in ("UP", "RANGE"):
            choch = "BEAR"

    a = atr(df, 14)
    atr_now = float(a.iloc[-1]) if len(a.dropna()) else None

    # ATR regime via percentile of last 200 bars
    atr_pctile = None
    regime = "NORMAL"
    recent = a.dropna().tail(200)
    if atr_now is not None and len(recent) >= 50:
        atr_pctile = float((recent <= atr_now).mean() * 100.0)
        if atr_pctile >= 75:
            regime = "HIGH"
        elif atr_pctile <= 25:
            regime = "LOW"

    return StructureSummary(
        last_pivot_high=last_ph,
        last_pivot_low=last_pl,
        bos=bos,
        choch=choch,
        trend=trend,
        atr=atr_now,
        atr_pctile=atr_pctile,
        atr_regime=regime,
    )
