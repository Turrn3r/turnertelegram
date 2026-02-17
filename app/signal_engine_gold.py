from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Any
import pandas as pd
from .config import TP1_R, TP2_R


@dataclass
class TradeIdea:
    direction: str
    entry: Optional[Tuple[float, float]]
    sl: Optional[float]
    tp1: Optional[float]
    tp2: Optional[float]
    rr: Optional[float]
    confidence: int
    reasons: List[str]


def _ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def _rsi(close, period=14):
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.ewm(alpha=1/period, adjust=False).mean()
    ma_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = ma_up / (ma_down + 1e-12)
    return 100 - (100 / (1 + rs))


def _atr(df, period=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    tr = pd.concat([
        (high - low),
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def build_trade_idea(df: pd.DataFrame, catalysts: Dict[str, Any], min_conf: int) -> TradeIdea:

    if df.empty or len(df) < 240:
        return TradeIdea("NO_TRADE", None, None, None, None, None, 0, ["Not enough history"])

    close = df["Close"]
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)
    rsi = _rsi(close)
    atr = _atr(df)

    last = float(close.iloc[-1])
    a = float(atr.iloc[-1])
    r = float(rsi.iloc[-1])

    trend_up = ema20.iloc[-1] > ema50.iloc[-1] > ema200.iloc[-1]
    trend_dn = ema20.iloc[-1] < ema50.iloc[-1] < ema200.iloc[-1]

    pullback_20 = abs(last - ema20.iloc[-1]) < 0.6 * a
    pullback_50 = abs(last - ema50.iloc[-1]) < 1.0 * a

    reasons = []
    score = 0

    if trend_up:
        score += 30
        reasons.append("Trend up (EMA stack)")
    if trend_dn:
        score += 30
        reasons.append("Trend down (EMA stack)")

    if r > 52:
        score += 10
        reasons.append("Momentum bullish")
    if r < 48:
        score += 10
        reasons.append("Momentum bearish")

    if pullback_20:
        score += 18
        reasons.append("Near EMA20 pullback")
    elif pullback_50:
        score += 12
        reasons.append("Near EMA50 pullback")

    news_score = catalysts.get("news_score", 0)
    macro_score = catalysts.get("macro_score", 0)
    bias = catalysts.get("bias", "NEUTRAL")

    if news_score > 0.3:
        score += 10
        reasons.append("High-relevance news")

    if macro_score > 0.3:
        score += 10
        reasons.append("Macro proximity")

    if bias == "BULL":
        score += 6
    elif bias == "BEAR":
        score += 6

    score = min(100, score)

    direction = "NO_TRADE"

    if trend_up and r > 52 and bias != "BEAR":
        direction = "LONG"
    elif trend_dn and r < 48 and bias != "BULL":
        direction = "SHORT"

    if direction == "NO_TRADE" or score < min_conf:
        return TradeIdea("NO_TRADE", None, None, None, None, None, score, reasons)

    entry_mid = ema20.iloc[-1] if pullback_20 else ema50.iloc[-1]

    if direction == "LONG":
        entry = (entry_mid - 0.3*a, entry_mid + 0.1*a)
        sl = entry[0] - a
        tp1 = entry_mid + TP1_R*(entry_mid - sl)
        tp2 = entry_mid + TP2_R*(entry_mid - sl)
        rr = (tp1 - entry_mid) / (entry_mid - sl)
    else:
        entry = (entry_mid - 0.1*a, entry_mid + 0.3*a)
        sl = entry[1] + a
        tp1 = entry_mid - TP1_R*(sl - entry_mid)
        tp2 = entry_mid - TP2_R*(sl - entry_mid)
        rr = (entry_mid - tp1) / (sl - entry_mid)

    return TradeIdea(direction, entry, sl, tp1, tp2, rr, score, reasons)
