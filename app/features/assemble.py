from __future__ import annotations
from typing import Any
import pandas as pd

from .regime import classify_regime
from .indicators import rsi, atr, swing_levels
from .catalysts import news_bias, macro_relevance
from .flow_proxy import flow_proxy


def assemble_features(
    df: pd.DataFrame,
    news: list[dict[str, Any]],
    macro: list[dict[str, Any]],
) -> dict[str, Any]:
    reg = classify_regime(df)

    # compute RSI for decision engine
    r = float(rsi(df["Close"], 14).iloc[-1]) if len(df) >= 20 else 50.0
    a = float(atr(df, 14).iloc[-1]) if len(df) >= 20 else 0.0
    sh, sl = swing_levels(df, 240)

    titles = [n.get("title", "") for n in news]
    bias, news_score = news_bias(titles)
    macro_score = macro_relevance(macro)

    flow = flow_proxy(df)

    return {
        "regime": reg,
        "market": {
            "rsi": r,
            "atr": a,
            "swing_high": sh,
            "swing_low": sl,
        },
        "catalysts": {
            "bias": bias,
            "news_score": float(news_score),
            "macro_score": float(macro_score),
            "top_news": titles[:6],
            "macro_events": macro[:6],
        },
        "flow": flow,
    }
