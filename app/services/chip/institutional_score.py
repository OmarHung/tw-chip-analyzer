"""Institutional Score（法人/信用/借券）。見 docs/03 §10。輸出 -1..1。

margin/sbl/short 權重在 config 內即為負值，故一律以加法組合。
"""
from __future__ import annotations

from app.models.signal import DailyFeatures
from app.services.normalize import squash_z


def institutional_score(f: DailyFeatures, w: dict) -> float:
    return (
        w["trust"] * squash_z(f.trust_5d_z)
        + w["foreign"] * squash_z(f.foreign_5d_z)
        + w["dealer"] * squash_z(f.dealer_5d_z)
        + w["margin_change"] * squash_z(f.margin_balance_change_z)
        + w["sbl_change"] * squash_z(f.sbl_change_z)
        + w["short_change"] * squash_z(f.short_balance_change_z)
    )
