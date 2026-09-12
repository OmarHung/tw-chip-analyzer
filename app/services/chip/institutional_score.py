"""Institutional Score（法人/信用/借券）。見 docs/03 §10。輸出 -1..1。

margin/sbl/short 權重在 config 內即為負值，故一律以加法組合。

子項缺值（None）時依 |權重| 比例在成分內重分配（docs/09 BUG-11），使「只有部分子項
有資料」的分數尺度與完整資料一致；有效子項（權重非 0）全缺 → 回 None，由 composite
排除整個 institutional 成分，不以中性 0 冒充有資料。
"""
from __future__ import annotations

from app.models.signal import DailyFeatures
from app.services.normalize import squash_z


def _items(f: DailyFeatures, w: dict) -> list[tuple[float, float | None]]:
    return [
        (w["trust"], f.trust_5d_z),
        (w["foreign"], f.foreign_5d_z),
        (w["dealer"], f.dealer_5d_z),
        (w["margin_change"], f.margin_balance_change_z),
        (w["sbl_change"], f.sbl_change_z),
        (w["short_change"], f.short_balance_change_z),
    ]


def institutional_score(f: DailyFeatures, w: dict) -> float | None:
    items = _items(f, w)
    total = sum(abs(wt) for wt, _ in items)
    present = [(wt, z) for wt, z in items if z is not None and wt != 0]
    present_weight = sum(abs(wt) for wt, _ in present)
    if not present or total == 0:
        return None
    return sum(wt * squash_z(z) for wt, z in present) * (total / present_weight)
