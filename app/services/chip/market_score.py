"""Market Score（大盤/產業環境）。見 docs/03 §10。輸出 -1..1。

market_trend_score / industry_trend_score 已為 -1..1（由大盤 MA、漲跌家數等彙整）。
"""
from __future__ import annotations

from app.models.signal import MarketContext
from app.services.normalize import clamp


def market_score(m: MarketContext, w: dict) -> float:
    """大盤未知時僅以 0 參與計算；是否把 market 列為有效成分由 analysis 依可用性決定。"""
    trend = 0.0 if m.market_trend_score is None else m.market_trend_score
    return w["market_trend"] * clamp(trend) + w["industry_trend"] * clamp(m.industry_trend_score)
