"""Exit Logic。見 docs/04 §14。持倉中的出場判斷。"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Thresholds, get_thresholds
from app.models.signal import Action


@dataclass
class ExitContext:
    price: float
    stop_loss: float
    entry_price: float | None = None  # 持倉成本；有值時 TP 以成本為基準
    price_new_high: bool = False
    # 出貨警示所需的「真實」單股訊號；None = 無可靠來源（不可用橫斷面 z 值替代）
    cvd_slope: float | None = None
    large_trade_delta: float | None = None
    buy_absorption_rising: bool | None = None


def decide_exit(
    score: float, ctx: ExitContext, thresholds: Thresholds | None = None
) -> tuple[Action, list[str]]:
    t = thresholds or get_thresholds()
    sig = t.signal
    ex = t.exit_rules
    reasons: list[str] = []

    # 1) Hard stop
    if ctx.price <= ctx.stop_loss:
        return Action.EXIT, ["跌破停損價"]

    # 2) 籌碼惡化
    if score < sig["exit_score"]:
        return Action.EXIT, ["籌碼分數跌破出場門檻"]
    if score < sig["reduce_score"]:
        return Action.REDUCE, ["籌碼分數轉弱，建議減碼"]

    # 3) Distribution Warning（出貨警示）：預設關閉，且三個真實訊號都有值才判斷（docs/12 Phase 3A）
    signals_known = None not in (ctx.cvd_slope, ctx.large_trade_delta, ctx.buy_absorption_rising)
    if (
        ex.get("distribution_enabled", False)
        and signals_known
        and ctx.price_new_high
        and ctx.cvd_slope <= ex["distribution_cvd_slope_max"]
        and ctx.large_trade_delta <= ex["distribution_large_delta_max"]
        and ctx.buy_absorption_rising
    ):
        reasons.append("價創高但買盤/大單背離，出現出貨警示")
        return Action.REDUCE, reasons

    return Action.HOLD, reasons
