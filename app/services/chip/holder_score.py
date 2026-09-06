"""Holder Score（TDCC 集中度變化）。見 docs/03 §10。輸出 -1..1。"""
from __future__ import annotations

from app.models.signal import WeeklyFeatures
from app.services.normalize import squash_z


def holder_score(f: WeeklyFeatures, w: dict) -> float:
    return (
        w["large_holder_change"] * squash_z(f.large_holder_ratio_change_z)
        + w["retail_holder_change"] * squash_z(f.retail_holder_ratio_change_z)
        + w["holder_count_change"] * squash_z(f.holder_count_change_z)
    )
