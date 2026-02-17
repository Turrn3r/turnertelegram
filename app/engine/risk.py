from __future__ import annotations
from dataclasses import dataclass


@dataclass
class RiskPlan:
    entry_low: float
    entry_high: float
    sl: float
    tp1: float
    tp2: float
    rr: float


def build_atr_risk_plan(
    direction: str,
    entry_mid: float,
    atr: float,
    sl_atr_mult: float,
    tp1_r: float,
    tp2_r: float,
) -> RiskPlan:
    if direction == "LONG":
        entry_low = entry_mid - 0.30 * atr
        entry_high = entry_mid + 0.10 * atr
        sl = entry_low - sl_atr_mult * atr
        risk = max(entry_mid - sl, 1e-12)
        tp1 = entry_mid + tp1_r * risk
        tp2 = entry_mid + tp2_r * risk
        rr = (tp1 - entry_mid) / risk
    else:
        entry_low = entry_mid - 0.10 * atr
        entry_high = entry_mid + 0.30 * atr
        sl = entry_high + sl_atr_mult * atr
        risk = max(sl - entry_mid, 1e-12)
        tp1 = entry_mid - tp1_r * risk
        tp2 = entry_mid - tp2_r * risk
        rr = (entry_mid - tp1) / risk

    return RiskPlan(entry_low, entry_high, sl, tp1, tp2, rr)
