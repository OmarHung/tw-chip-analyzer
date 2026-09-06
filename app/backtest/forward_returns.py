"""Forward return / MFE / MAE 計算（見 docs/06 §17）。

定義：進場價 = entry bar 的 open（或 close，config）。
持有 k 個交易日 → 出場於第 k 根 bar 的 close（第 1 天即進場當根）。
MFE/MAE 於 bars[0..k-1] 以 high/low 相對進場價計算。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import datetime as dt

from app.backtest.costs import CostModel


@dataclass
class Bar:
    date: dt.date
    open: float
    high: float
    low: float
    close: float


@dataclass
class HorizonResult:
    horizon: int
    gross_return: float
    net_return: float
    mfe: float  # 最大有利變動（相對進場價）
    mae: float  # 最大不利變動（<=0）


@dataclass
class ForwardResult:
    entry_date: dt.date
    entry_price: float
    horizons: dict[int, HorizonResult] = field(default_factory=dict)
    dropped_horizons: list[int] = field(default_factory=list)  # 資料不足未計算


def compute_forward(
    entry_price: float,
    future_bars: Sequence[Bar],
    horizons: Sequence[int],
    costs: CostModel,
    entry_date: dt.date,
) -> ForwardResult:
    """future_bars：從 entry bar 起（含）依日期排序的後續 bars。"""
    res = ForwardResult(entry_date=entry_date, entry_price=entry_price)
    n = len(future_bars)
    for k in sorted(horizons):
        if k > n:
            res.dropped_horizons.append(k)
            continue
        window = future_bars[:k]
        exit_price = window[-1].close
        gross = (exit_price - entry_price) / entry_price
        net = costs.net_return(entry_price, exit_price)
        mfe = max((b.high - entry_price) / entry_price for b in window)
        mae = min((b.low - entry_price) / entry_price for b in window)
        res.horizons[k] = HorizonResult(k, gross, net, mfe, mae)
    return res
