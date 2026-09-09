"""回測績效指標（見 docs/06 §17）。"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass
class ReturnStats:
    count: int
    win_rate: float
    avg_return: float
    median_return: float
    profit_factor: float
    expectancy: float
    max_drawdown: float
    sharpe: float
    sortino: float
    avg_mfe: float
    avg_mae: float


def _profit_factor(returns: np.ndarray) -> float:
    wins = returns[returns > 0].sum()
    losses = -returns[returns < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / losses)


def _max_drawdown(returns: Sequence[float]) -> float:
    """以序列交易的累積權益曲線計算最大回撤（回傳負值或 0）。"""
    equity = np.cumsum(returns)
    if equity.size == 0:
        return 0.0
    running_max = np.maximum.accumulate(equity)
    drawdown = equity - running_max
    return float(drawdown.min())


def _sharpe(returns: np.ndarray) -> float:
    if returns.size < 2:
        return 0.0
    std = returns.std(ddof=1)
    return float(returns.mean() / std) if std > 0 else 0.0


def _sortino(returns: np.ndarray) -> float:
    if returns.size < 2:
        return 0.0
    downside = returns[returns < 0]
    dd = downside.std(ddof=1) if downside.size >= 2 else 0.0
    return float(returns.mean() / dd) if dd > 0 else 0.0


def summarize(
    returns: Sequence[float],
    mfes: Sequence[float] | None = None,
    maes: Sequence[float] | None = None,
) -> ReturnStats:
    r = np.asarray(returns, dtype=float)
    if r.size == 0:
        return ReturnStats(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    return ReturnStats(
        count=int(r.size),
        win_rate=float((r > 0).mean()),
        avg_return=float(r.mean()),
        median_return=float(np.median(r)),
        profit_factor=_profit_factor(r),
        expectancy=float(r.mean()),
        max_drawdown=_max_drawdown(r),
        sharpe=_sharpe(r),
        sortino=_sortino(r),
        avg_mfe=float(np.mean(mfes)) if mfes is not None and len(mfes) else 0.0,
        avg_mae=float(np.mean(maes)) if maes is not None and len(maes) else 0.0,
    )


def newey_west_t(values: Sequence[float], lags: int) -> float | None:
    """逐日 IC 序列的 Newey-West（HAC）t 值。樣本 <2 回 None。

    為什麼需要：H 日 forward return 在**連續交易日之間高度重疊**（今天與明天的
    20 日報酬共用 19 天），逐日 IC 因此強烈自相關。用 `mean/std*sqrt(n)` 的樸素
    t 值把 n 天當成 n 個獨立樣本，會系統性高估顯著性——實測 SBL 因子樸素 t=-3.81，
    但 35 個 test 日其實只含約 1.75 個不重疊的 20 日區塊。

    Newey-West 以 Bartlett 權重 `1 - l/(lags+1)` 累加前 lags 階自協方差，得到對
    自相關穩健的變異數。`lags` 取 forward horizon − 1（重疊長度）。
    """
    a = np.asarray(values, dtype=float)
    n = a.size
    if n < 2:
        return None
    dev = a - a.mean()
    var = float(dev @ dev) / n                      # γ0
    for lag in range(1, min(lags, n - 1) + 1):
        gamma = float(dev[lag:] @ dev[:-lag]) / n
        var += 2.0 * (1.0 - lag / (lags + 1.0)) * gamma
    if var <= 0:                                    # 負變異數（小樣本可能發生）
        return 0.0
    return float(a.mean() / np.sqrt(var / n))
