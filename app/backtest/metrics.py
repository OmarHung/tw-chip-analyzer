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
