"""交易成本模型（見 docs/06 §23、§17）。禁止 0 成本回測。

台股：每邊各收券商手續費；賣出另收證交稅；兩邊各計滑價。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_thresholds


@dataclass(frozen=True)
class CostModel:
    fee_rate: float = 0.001425   # 單邊手續費
    tax_rate: float = 0.003      # 證交稅（僅賣出）
    slippage_pct: float = 0.001  # 單邊滑價

    @classmethod
    def from_config(cls) -> "CostModel":
        c = get_thresholds().backtest.get("costs", {})
        return cls(
            fee_rate=c.get("fee_rate", 0.001425),
            tax_rate=c.get("tax_rate", 0.003),
            slippage_pct=c.get("slippage_pct", 0.001),
        )

    @property
    def buy_cost_rate(self) -> float:
        return self.fee_rate + self.slippage_pct

    @property
    def sell_cost_rate(self) -> float:
        return self.fee_rate + self.tax_rate + self.slippage_pct

    def net_return(self, entry_price: float, exit_price: float) -> float:
        """含成本的實際報酬率（買進付 buy_cost，賣出扣 sell_cost）。"""
        if entry_price <= 0:
            return 0.0
        paid = entry_price * (1 + self.buy_cost_rate)
        received = exit_price * (1 - self.sell_cost_rate)
        return (received - paid) / paid

    @property
    def round_trip_cost(self) -> float:
        """一買一賣的總成本率（近似，供快速估算）。"""
        return self.buy_cost_rate + self.sell_cost_rate
