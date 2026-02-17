from __future__ import annotations
from dataclasses import dataclass
from typing import Any, List

from .risk import build_atr_risk_plan, RiskPlan


@dataclass
class Decision:
    direction: str  # LONG/SHORT/NO_TRADE
    confidence: int
    reasons: List[str]
    risk: RiskPlan | None
    snapshot: dict[str, Any]


def decide(features: dict[str, Any], settings) -> Decision:
    reg = features["regime"]
    mkt = features["market"]
    cat = features["catalysts"]
    flow = features["flow"]

    trend = reg["trend"]
    rsi = float(mkt["rsi"])
    atr = float(mkt["atr"])
    ema20 = float(reg["ema20"])
    ema50 = float(reg["ema50"])
    last = float(reg["last"])

    # pullback proximity
    near20 = abs(last - ema20) <= 0.60 * atr if atr > 0 else False
    near50 = abs(last - ema50) <= 0.95 * atr if atr > 0 else False

    # catalyst + flow
    bias = cat["bias"]
    news_score = float(cat["news_score"])
    macro_score = float(cat["macro_score"])
    flow_score = float(flow.get("flow_score", 0.0))

    score = 0
    reasons: List[str] = []

    # Trend
    if trend == "UP":
        score += 30
        reasons.append("Trend UP (EMA stack)")
    elif trend == "DOWN":
        score += 30
        reasons.append("Trend DOWN (EMA stack)")
    else:
        score += 10
        reasons.append("Mixed trend (lower conviction)")

    # Momentum
    if rsi >= 54:
        score += 12
        reasons.append(f"RSI bullish ({rsi:.1f})")
    elif rsi <= 46:
        score += 12
        reasons.append(f"RSI bearish ({rsi:.1f})")
    else:
        score += 5
        reasons.append(f"RSI neutral ({rsi:.1f})")

    # Location
    if near20:
        score += 18
        reasons.append("Pullback near EMA20")
    elif near50:
        score += 12
        reasons.append("Pullback near EMA50")
    else:
        score += 3
        reasons.append("Not at a preferred pullback zone")

    # Catalysts
    if news_score >= 0.35:
        score += 10
        reasons.append("High-relevance global news catalyst")
    if macro_score >= 0.35:
        score += 10
        reasons.append("Macro-event relevance elevated")

    # Flow proxy
    if flow_score >= 0.35:
        score += 10
        reasons.append(f"Flow proxy active ({flow.get('notes', [])})")

    # Bias alignment
    if bias == "BULL":
        score += 6
        reasons.append("Catalyst bias: BULL (gold supportive)")
    elif bias == "BEAR":
        score += 6
        reasons.append("Catalyst bias: BEAR (gold headwind)")

    score = min(100, score)
    confidence = int(score)

    # Direction logic (conservative)
    direction = "NO_TRADE"
    if trend == "UP" and rsi >= 52 and bias != "BEAR":
        direction = "LONG"
    elif trend == "DOWN" and rsi <= 48 and bias != "BULL":
        direction = "SHORT"

    # If not confident enough, no trade
    if direction == "NO_TRADE" or confidence < settings.min_confidence or atr <= 0:
        return Decision("NO_TRADE", confidence, reasons, None, features)

    entry_mid = ema20 if near20 else ema50
    risk = build_atr_risk_plan(
        direction=direction,
        entry_mid=float(entry_mid),
        atr=float(atr),
        sl_atr_mult=settings.sl_atr_mult,
        tp1_r=settings.tp1_r,
        tp2_r=settings.tp2_r,
    )

    return Decision(direction, confidence, reasons, risk, features)
