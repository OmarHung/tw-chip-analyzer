"""Market Score（大盤/產業環境）。見 docs/03 §10。輸出 -1..1。

market_trend_score / industry_trend_score 已為 -1..1（由大盤 MA、漲跌家數等彙整）。
"""
from __future__ import annotations

from app.models.signal import MarketContext
from app.services.normalize import clamp


def market_score(m: MarketContext, w: dict) -> float:
    return w["market_trend"] * clamp(m.market_trend_score) + w[
        "industry_trend"
    ] * clamp(m.industry_trend_score)
