from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
import math
import pandas as pd


@dataclass
class TradeIdea:
    direction: str               # "LONG" / "SHORT" / "NO_TRADE"
    entry: Optional[Tuple[float, float]]  # entry zone (low, high)
    sl: Optional[float]
    tp1: Optional[float]
    tp2: Optional[float]
    rr: Optional[float]
    confidence: int              # 0..100
    reasons: list[str]


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


def _swing_levels(df: pd.DataFrame, lookback: int = 120) -> tuple[Optional[float], Optional[float]]:
    """
    Simple swing high/low from last N bars (for TP/SL anchoring).
    """
    if len(df) < 10:
        return None, None
    sub = df.tail(lookback)
    return float(sub["High"].max()), float(sub["Low"].min())


def build_trade_idea(
    df_1m: pd.DataFrame,
    ob_features: Dict[str, Any] | None,
    min_confidence: int = 70
) -> TradeIdea:
    """
    df_1m: index datetime, columns Open/High/Low/Close
    ob_features: orderbook signals (imbalance, spread_bps, wall_side, wall_usd, depth deltas)
    """
    if df_1m is None or df_1m.empty or len(df_1m) < 60:
        return TradeIdea("NO_TRADE", None, None, None, None, None, 0, ["Not enough candle history"])

    close = df_1m["Close"]
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)
    rsi14 = _rsi(close, 14)
    atr14 = _atr(df_1m, 14)

    last = float(close.iloc[-1])
    last_ema20 = float(ema20.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])
    last_ema200 = float(ema200.iloc[-1])
    last_rsi = float(rsi14.iloc[-1])
    last_atr = float(atr14.iloc[-1])

    swing_high, swing_low = _swing_levels(df_1m, lookback=180)

    # Trend regime
    bull = last_ema20 >= last_ema50 >= last_ema200
    bear = last_ema20 <= last_ema50 <= last_ema200

    # Momentum bias
    mom_long = last_rsi >= 52
    mom_short = last_rsi <= 48

    # Pullback proximity to EMA20/EMA50 (good entry zones)
    dist20 = (last - last_ema20) / (last_atr + 1e-12)
    dist50 = (last - last_ema50) / (last_atr + 1e-12)

    near_ema20 = abs(dist20) <= 0.6
    near_ema50 = abs(dist50) <= 0.9

    # Order book confluence (optional)
    ob_score = 0
    reasons = []

    if ob_features:
        imb = float(ob_features.get("imbalance", 0.0))
        spread = float(ob_features.get("spread_bps", 0.0))
        wall_side = str(ob_features.get("top_wall_side", "NONE"))
        wall_usd = float(ob_features.get("top_wall_usd", 0.0))
        d_bid = float(ob_features.get("delta_bid_depth_usd", 0.0))
        d_ask = float(ob_features.get("delta_ask_depth_usd", 0.0))

        # interesting: tight spread, supportive imbalance, wall confirmation, and liquidity add on the right side
        if spread <= 10:
            ob_score += 8
            reasons.append(f"Tight spread ({spread:.1f} bps)")

        if imb >= 0.18:
            ob_score += 10
            reasons.append(f"Bid imbalance +{imb:.2f}")
        elif imb <= -0.18:
            ob_score += 10
            reasons.append(f"Ask imbalance {imb:.2f}")

        if wall_side in ("BID", "ASK") and wall_usd >= 200_000:
            ob_score += 10
            reasons.append(f"{wall_side} wall {_fmt_notional(wall_usd)}")

        if d_bid >= 150_000:
            ob_score += 8
            reasons.append(f"Liquidity added to bids ({_fmt_notional(d_bid)})")
        if d_ask >= 150_000:
            ob_score += 8
            reasons.append(f"Liquidity added to asks ({_fmt_notional(d_ask)})")

    # Core scoring
    score = 0
    if bull:
        score += 25
        reasons.append("Bull trend (EMA20≥EMA50≥EMA200)")
    if bear:
        score += 25
        reasons.append("Bear trend (EMA20≤EMA50≤EMA200)")

    if mom_long:
        score += 10
        reasons.append(f"RSI supports long ({last_rsi:.1f})")
    if mom_short:
        score += 10
        reasons.append(f"RSI supports short ({last_rsi:.1f})")

    if near_ema20:
        score += 15
        reasons.append("Near EMA20 (pullback/mean reversion zone)")
    elif near_ema50:
        score += 10
        reasons.append("Near EMA50 (deeper pullback zone)")

    score += ob_score
    score = int(max(0, min(100, score)))

    # Direction decision
    direction = "NO_TRADE"
    if bull and mom_long:
        direction = "LONG"
    elif bear and mom_short:
        direction = "SHORT"

    # Build entry/TP/SL only if strong enough
    if direction == "NO_TRADE" or score < min_confidence:
        return TradeIdea("NO_TRADE", None, None, None, None, None, score, reasons + [f"Confidence < {min_confidence}"])

    # Entry zone: around EMA20/EMA50 with ATR padding
    if direction == "LONG":
        entry_mid = last_ema20 if near_ema20 else last_ema50
        entry_low = entry_mid - 0.25 * last_atr
        entry_high = entry_mid + 0.10 * last_atr

        # SL: below recent swing low or ATR-based
        sl = min(entry_low - 0.8 * last_atr, (swing_low - 0.25 * last_atr) if swing_low else entry_low - 1.1 * last_atr)

        # TP: first at prior swing high region; second at RR extension
        tp1 = (swing_high - 0.10 * last_atr) if swing_high else entry_mid + 1.5 * last_atr
        tp2 = entry_mid + 2.5 * last_atr

        risk = entry_mid - sl
        rr = ((tp1 - entry_mid) / (risk + 1e-12)) if risk > 0 else None

    else:  # SHORT
        entry_mid = last_ema20 if near_ema20 else last_ema50
        entry_low = entry_mid - 0.10 * last_atr
        entry_high = entry_mid + 0.25 * last_atr

        sl = max(entry_high + 0.8 * last_atr, (swing_high + 0.25 * last_atr) if swing_high else entry_high + 1.1 * last_atr)

        tp1 = (swing_low + 0.10 * last_atr) if swing_low else entry_mid - 1.5 * last_atr
        tp2 = entry_mid - 2.5 * last_atr

        risk = sl - entry_mid
        rr = ((entry_mid - tp1) / (risk + 1e-12)) if risk > 0 else None

    return TradeIdea(
        direction=direction,
        entry=(float(entry_low), float(entry_high)),
        sl=float(sl),
        tp1=float(tp1),
        tp2=float(tp2),
        rr=float(rr) if rr is not None else None,
        confidence=score,
        reasons=reasons
    )


def _fmt_notional(x: float) -> str:
    if x >= 1_000_000_000:
        return f"${x/1_000_000_000:.2f}B"
    if x >= 1_000_000:
        return f"${x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x/1_000:.1f}K"
    return f"${x:,.0f}"
