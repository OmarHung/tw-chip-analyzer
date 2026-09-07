"""§28 成功標準驗證:Chip Score 越高,forward return 是否單調改善?且 MAE 不惡化。

用真實 signal_snapshot(全量)+ daily_price,經 look-ahead 安全引擎評估,輸出:
- 每個 horizon 的 score bucket 表(win% / avg / median / PF / avgMAE)
- 每個 horizon 的 Spearman 等級相關 IC(score vs net_return)+ 樣本數

用法:
    APP_ENV=dev python -m scripts.score_monotonicity [--min-turnover 0]

--min-turnover:以進場前一日(=data_date 當日)成交金額過濾低流動性標的(預設 0=不濾)。
"""
from __future__ import annotations

import argparse
import asyncio
import math

from sqlalchemy import select

from app.backtest import BacktestEngine, format_bucket_table
from app.backtest.runner import load_bars, load_signals
from app.db.models.market import DailyPrice
from app.db.session import get_sessionmaker


def _ranks(vals: list[float]) -> list[float]:
    """平均等級(處理同分:取平均 rank)。"""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based 平均 rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman 等級相關 = 等級後的 Pearson。"""
    n = len(xs)
    if n < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


async def _turnover_by_key() -> dict[tuple[str, object], float]:
    """(symbol, data_date) -> turnover,用於流動性過濾。"""
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                select(DailyPrice.symbol, DailyPrice.data_date, DailyPrice.turnover)
            )
        ).all()
    return {(sym, d): float(t) for sym, d, t in rows if t is not None}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-turnover", type=float, default=0.0)
    args = ap.parse_args()

    sm = get_sessionmaker()
    async with sm() as s:
        signals = await load_signals(s)
        symbols = sorted({sig.symbol for sig in signals})
        prices = await load_bars(s, symbols)

    if args.min_turnover > 0:
        tk = await _turnover_by_key()
        signals = [
            sig for sig in signals
            if tk.get((sig.symbol, sig.data_date), 0.0) >= args.min_turnover
        ]

    engine = BacktestEngine()
    report = engine.run(signals, prices)

    print(
        f"訊號={report.total_signals} 已評估={report.evaluated} "
        f"略過={report.dropped} min_turnover={args.min_turnover:,.0f}"
    )

    # 單一 pass 收集每筆訊號各 horizon 的 (score, net_return),供 IC。
    scores_by_h: dict[int, list[float]] = {h: [] for h in engine.horizons}
    rets_by_h: dict[int, list[float]] = {h: [] for h in engine.horizons}
    bar_cache = {sym: list(bars) for sym, bars in prices.items()}
    for sig in signals:
        bars = bar_cache.get(sig.symbol)
        if not bars:
            continue
        oc = engine.evaluate_signal(sig, bars)
        if oc is None:
            continue
        for h in engine.horizons:
            hr = oc.forward.horizons.get(h)
            if hr is not None:
                scores_by_h[h].append(sig.chip_score)
                rets_by_h[h].append(hr.net_return)

    # 逐 horizon:bucket 表 + IC
    for h in engine.horizons:
        print(format_bucket_table(report, horizon=h))
        xs, ys = scores_by_h[h], rets_by_h[h]
        rho = _spearman(xs, ys) if len(xs) >= 30 else None
        if rho is not None:
            print(f"  IC(Spearman, {h}D) = {rho:+.4f}  n={len(xs)}")
        else:
            print(f"  IC({h}D): 樣本不足({len(xs)})")
        print()


if __name__ == "__main__":
    asyncio.run(main())
