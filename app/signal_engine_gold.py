# app/signal_engine_gold.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Any
import pandas as pd

from .config import TP1_R, TP2_R


@dataclass
class TradeIdea:
    direction: str  # LONG / SHORT / NO_TRADE
    entry: Optional[Tuple[float, float]]
    sl: Optional[float]
    tp1: Optional[float]
    tp2: Optional[float]
    rr1: Optional[float]
    confidence: int
    reasons: List[str]


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.ewm(alpha=1/period, adjust=False).mean()
    ma_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = ma_up / (ma_down + 1e-12)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    tr = pd.concat([
        (high - low),
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def build_trade_idea(
    df: pd.DataFrame,
    catalysts: Dict[str, Any],
    min_conf: int = 80
) -> TradeIdea:
    if df is None or df.empty or len(df) < 240:
        return TradeIdea("NO_TRADE", None, None, None, None, None, 0, ["Not enough history"])

    close = df["Close"]
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)
    rsi = _rsi(close, 14)
    atr = _atr(df, 14)

    last = float(close.iloc[-1])
    a = float(atr.iloc[-1])
    if a <= 0:
        return TradeIdea("NO_TRADE", None, None, None, None, None, 0, ["ATR invalid"])

    trend_up = float(ema20.iloc[-1]) >= float(ema50.iloc[-1]) >= float(ema200.iloc[-1])
    trend_dn = float(ema20.iloc[-1]) <= float(ema50.iloc[-1]) <= float(ema200.iloc[-1])

    r = float(rsi.iloc[-1])
    momentum_long = r >= 52
    momentum_short = r <= 48

    # Pullback zones
    d20 = (last - float(ema20.iloc[-1])) / a
    d50 = (last - float(ema50.iloc[-1])) / a
    near_20 = abs(d20) <= 0.6
    near_50 = abs(d50) <= 0.9

    # Catalyst scoring
    score = 0
    reasons: List[str] = []

    news_score = float(catalysts.get("news_score", 0.0))
    macro_score = float(catalysts.get("macro_score", 0.0))
    catalyst_bias = str(catalysts.get("bias", "NEUTRAL"))  # BULL / BEAR / NEUTRAL

    if trend_up:
        score += 28
        reasons.append("Trend up (EMA stack)")
    if trend_dn:
        score += 28
        reasons.append("Trend down (EMA stack)")

    if momentum_long:
        score += 10
        reasons.append(f"Momentum long (RSI {r:.1f})")
    if momentum_short:
        score += 10
        reasons.append(f"Momentum short (RSI {r:.1f})")

    if near_20:
        score += 18
        reasons.append("Near EMA20 (good pullback zone)")
    elif near_50:
        score += 12
        reasons.append("Near EMA50 (deeper pullback zone)")

    # catalysts influence
    if news_score >= 0.35:
        score += 10
        reasons.append("News catalyst present")
    if macro_score >= 0.35:
        score += 10
        reasons.append("Macro catalyst proximity/importance")

    # bias alignment
    if catalyst_bias == "BULL":
        score += 6
        reasons.append("Catalyst bias bullish for gold")
    elif catalyst_bias == "BEAR":
        score += 6
        reasons.append("Catalyst bias bearish for gold")

    score = int(max(0, min(100, score)))

    direction = "NO_TRADE"
    if trend_up and momentum_long and catalyst_bias != "BEAR":
        direction = "LONG"
    elif trend_dn and momentum_short and catalyst_bias != "BULL":
        direction = "SHORT"

    if direction == "NO_TRADE" or score < min_conf:
        return TradeIdea("NO_TRADE", None, None, None, None, None, score, reasons + [f"Confidence < {min_conf}"])

    # Entry/SL/TP using ATR risk units
    entry_mid = float(ema20.iloc[-1]) if near_20 else float(ema50.iloc[-1])
    if direction == "LONG":
        entry = (entry_mid - 0.25 * a, entry_mid + 0.10 * a)
        sl = entry[0] - 1.0 * a
        tp1 = entry_mid + TP1_R * (entry_mid - sl)
        tp2 = entry_mid + TP2_R * (entry_mid - sl)
        rr1 = (tp1 - entry_mid) / max(entry_mid - sl, 1e-12)
    else:
        entry = (entry_mid - 0.10 * a, entry_mid + 0.25 * a)
        sl = entry[1] + 1.0 * a
        tp1 = entry_mid - TP1_R * (sl - entry_mid)
        tp2 = entry_mid - TP2_R * (sl - entry_mid)
        rr1 = (entry_mid - tp1) / max(sl - entry_mid, 1e-12)

    return TradeIdea(direction, (float(entry[0]), float(entry[1])), float(sl), float(tp1), float(tp2), float(rr1), score, reasons)
