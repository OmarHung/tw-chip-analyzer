"""對真實 signal_snapshot 跑 backtest,驗證 Chip Score 單調性。

成功標準(CLAUDE.md):Chip Score 越高,未來報酬有統計上單調改善,且 MAE 不惡化。
輸出 score bucket × 多 horizon 的績效表 + threshold 表(含交易成本,禁 0 成本)。

用法:APP_ENV=dev python -m scripts.backtest_real
"""
from __future__ import annotations

import asyncio

from app.backtest import BacktestEngine, format_bucket_table
from app.backtest.runner import run_db_backtest
from app.db.session import get_sessionmaker


async def _amain() -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        report = await run_db_backtest(s, BacktestEngine())

    for h in report.horizons:
        print(format_bucket_table(report, horizon=h))
        print()

    print("=== Score Threshold × 各 horizon(net avg / win% / n)===")
    hs = report.horizons
    header = "thr   " + "".join(f"{f'{h}D':>18}" for h in hs)
    print(header)
    for br in report.by_threshold:
        cells = []
        for h in hs:
            st = br.by_horizon.get(h)
            if st and st.count:
                cells.append(f"{st.avg_return:>7.2%}/{st.win_rate:>3.0%}/{st.count:<4}")
            else:
                cells.append(f"{'-':>18}")
        print(f"{br.label:<6}" + "".join(f"{c:>18}" for c in cells))


if __name__ == "__main__":
    asyncio.run(_amain())
