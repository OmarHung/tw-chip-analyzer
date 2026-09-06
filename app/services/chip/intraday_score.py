"""Intraday Score（盤中 order flow 綜合）。見 docs/03 §10。輸出 -1..1。"""
from __future__ import annotations

from app.models.signal import IntradayFeatures
from app.services.normalize import clamp, squash_z


def intraday_score(f: IntradayFeatures, w: dict) -> float:
    return (
        w["large_trade_delta"] * squash_z(f.large_trade_delta_z)
        + w["cvd"] * squash_z(f.cvd_z)
        + w["absorption"] * squash_z(f.absorption_z)
        + w["obi"] * clamp(f.obi)
        + w["trade_speed"] * squash_z(f.trade_speed_z)
        + w["price_efficiency"] * squash_z(f.price_efficiency_z)
    )
