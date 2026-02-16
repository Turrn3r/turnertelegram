from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class OrderBookSignal:
    symbol: str
    mid: float
    spread_bps: float
    bid_depth_usd: float
    ask_depth_usd: float
    imbalance: float  # (bid-ask)/(bid+ask) in [-1,1]
    top_wall_side: str  # BID/ASK/NONE
    top_wall_usd: float
    top_wall_price: float


async def fetch_binance_depth(client: httpx.AsyncClient, symbol: str = "XRPUSDT", limit: int = 1000) -> dict:
    url = "https://api.binance.com/api/v3/depth"
    params = {"symbol": symbol, "limit": min(max(limit, 5), 1000)}
    r = await client.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def _to_levels(levels: list[list[str]]) -> list[tuple[float, float]]:
    # [price, qty] as strings
    out: list[tuple[float, float]] = []
    for p, q in levels:
        try:
            out.append((float(p), float(q)))
        except Exception:
            continue
    return out


def analyze_depth(
    depth: dict,
    symbol: str = "XRPUSDT",
    depth_pct_band: float = 0.0025,  # 25 bps around mid
    wall_usd_threshold: float = 250_000.0,
) -> Optional[OrderBookSignal]:
    bids = _to_levels(depth.get("bids") or [])
    asks = _to_levels(depth.get("asks") or [])
    if not bids or not asks:
        return None

    best_bid = bids[0][0]
    best_ask = asks[0][0]
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0:
        return None

    spread_bps = ((best_ask - best_bid) / mid) * 10_000.0

    band_low = mid * (1.0 - depth_pct_band)
    band_high = mid * (1.0 + depth_pct_band)

    # Depth USD within band
    bid_depth_usd = 0.0
    ask_depth_usd = 0.0

    # Wall detection (largest single level USD)
    top_bid_wall = (0.0, 0.0)  # usd, price
    top_ask_wall = (0.0, 0.0)

    for price, qty in bids:
        if price < band_low:
            break
        usd = price * qty
        bid_depth_usd += usd
        if usd > top_bid_wall[0]:
            top_bid_wall = (usd, price)

    for price, qty in asks:
        if price > band_high:
            break
        usd = price * qty
        ask_depth_usd += usd
        if usd > top_ask_wall[0]:
            top_ask_wall = (usd, price)

    denom = bid_depth_usd + ask_depth_usd
    imbalance = 0.0 if denom == 0 else (bid_depth_usd - ask_depth_usd) / denom

    top_wall_side = "NONE"
    top_wall_usd = 0.0
    top_wall_price = 0.0

    if top_bid_wall[0] >= wall_usd_threshold or top_ask_wall[0] >= wall_usd_threshold:
        if top_bid_wall[0] >= top_ask_wall[0]:
            top_wall_side = "BID"
            top_wall_usd = top_bid_wall[0]
            top_wall_price = top_bid_wall[1]
        else:
            top_wall_side = "ASK"
            top_wall_usd = top_ask_wall[0]
            top_wall_price = top_ask_wall[1]

    return OrderBookSignal(
        symbol=symbol,
        mid=mid,
        spread_bps=spread_bps,
        bid_depth_usd=bid_depth_usd,
        ask_depth_usd=ask_depth_usd,
        imbalance=imbalance,
        top_wall_side=top_wall_side,
        top_wall_usd=top_wall_usd,
        top_wall_price=top_wall_price,
    )
