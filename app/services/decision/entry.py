"""Entry Filter。見 docs/04 §12。

鐵則：ChipScore 高 ≠ 可 BUY。必須通過所有 gate，否則降級為 WATCH（或 HOLD/AVOID）。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Thresholds, get_thresholds
from app.models.signal import Action
from app.services.decision.risk import RiskPlan


@dataclass
class PriceContext:
    """進場時機所需的價格/流動性/市場脈絡。"""

    close_vs_vwap_pct: float = 0.0   # (close-vwap)/vwap
    close_vs_ma20_pct: float = 0.0   # (close-ma20)/ma20
    turnover: float = 0.0            # 當日成交金額 TWD
    market_score_norm: float = 0.0   # -1..1（market_score 轉回 -1..1）
    is_locked_limit: bool = False    # 鎖死漲停等不可合理成交


@dataclass
class EntryDecision:
    action: Action
    gate_reasons: list[str]  # 未通過的原因（供 WATCH 說明）


def decide_entry(
    score: float,
    rr: float,
    ctx: PriceContext,
    thresholds: Thresholds | None = None,
) -> EntryDecision:
    t = thresholds or get_thresholds()
    sig = t.signal
    ef = t.entry_filter

    # 先決定分級底線
    if score < sig["exit_score"]:
        return EntryDecision(Action.AVOID, [])
    if score < sig["watch_score"]:
        return EntryDecision(Action.HOLD, [])

    # score >= watch_score：檢查是否夠格 BUY
    gates: list[str] = []
    if score < sig["buy_score"]:
        gates.append("分數未達 BUY 門檻")
    if rr < ef["min_risk_reward"]:
        gates.append(f"風險報酬不足（RR<{ef['min_risk_reward']}）")
    if ctx.close_vs_vwap_pct > ef["max_vwap_deviation_pct"]:
        gates.append("價格過度偏離 VWAP")
    if ctx.close_vs_ma20_pct > ef["max_ma20_deviation_pct"]:
        gates.append("價格過度偏離 MA20")
    if ctx.market_score_norm <= ef["strong_bear_market_score"]:
        gates.append("市場處於 Strong Bear")
    if ctx.turnover < ef["min_turnover"]:
        gates.append("流動性不足")
    if ctx.is_locked_limit:
        gates.append("鎖死漲停，無法合理成交")

    if not gates:
        return EntryDecision(Action.BUY, [])
    return EntryDecision(Action.WATCH, gates)


def entry_zone_for(action: Action, plan: RiskPlan) -> tuple[float, float] | None:
    if action in (Action.BUY, Action.WATCH):
        return (plan.entry_low, plan.entry_high)
    return None
