"""背離 × Chip Score 交叉驗證：背離是否提供獨立於分數的增量 alpha？（鐵則3 多訊號交叉）

用法：
    APP_ENV=dev python -m scripts.divergence_score_backtest [--window 60] [--step 5] \
        [--horizon 20] [--min-turnover 2e7]

方法（look-ahead 安全）：對每個 (股,日 D) 同時取
- chip_score（SignalSnapshot，D 當日盤後產出）
- 60 日量價背離狀態（data_date<=D 算）
進場取 >D 次日 open（engine），forward return 用 net。做 score bucket × 背離狀態
二維表，看：(a) 各分數段內 正背離−負背離 是否仍為正（背離增量）；
(b) 各背離欄內 高分−低分 是否為正（分數增量）。
"""
from __future__ import annotations

import argparse
import asyncio
import statistics as st
from collections import defaultdict

from sqlalchemy import select

from app.backtest.engine import BacktestEngine, BacktestSignal
from app.core.config import get_thresholds
from app.db.models.features import SignalSnapshot
from app.db.session import get_sessionmaker
from app.services.flows import compute_divergence
from scripts.divergence_backtest import _load

SCORE_BUCKETS = [(0, 50), (50, 60), (60, 70), (70, 101)]
# 背離收斂為三欄
DIV_COLS = [("bullish_div", "正背離"), ("_other", "其他"), ("bearish_div", "負背離")]


def _col(status: str) -> str:
    return status if status in ("bullish_div", "bearish_div") else "_other"


def _bucket(score: float) -> tuple[int, int] | None:
    for lo, hi in SCORE_BUCKETS:
        if lo <= score < hi:
            return (lo, hi)
    return None


async def _load_scores() -> dict[tuple[str, object], float]:
    sm = get_sessionmaker()
    out: dict[tuple[str, object], float] = {}
    async with sm() as s:
        rows = (
            await s.execute(
                select(SignalSnapshot.symbol, SignalSnapshot.data_date, SignalSnapshot.chip_score)
            )
        ).all()
    for sym, d, sc in rows:
        if sc is not None:
            out[(sym, d)] = float(sc)
    return out


def run(bars_by_sym, inst_by_sym, vol_by_sym, med_turnover, scores, *, window, step, horizon, min_turnover):
    th = get_thresholds()
    dcfg = th.divergence
    price_eps = float(dcfg.get("price_eps", 0.03))
    flow_eps = float(dcfg.get("flow_eps", 0.02))
    min_points = int(dcfg.get("min_points", 10))
    engine = BacktestEngine()
    horizons = engine.horizons
    max_h = max(horizons)

    # (score_bucket, div_col) -> list[net_return at horizon]
    cell: dict[tuple[tuple[int, int], str], list[float]] = defaultdict(list)

    for sym in sorted(bars_by_sym):
        if med_turnover.get(sym, 0.0) < min_turnover:
            continue
        bars = bars_by_sym[sym]
        n = len(bars)
        if n < window + max_h + 1:
            continue
        idate = inst_by_sym.get(sym, {})
        vdate = vol_by_sym.get(sym, {})
        closes = [b.close for b in bars]
        inst_arr = [idate.get(b.date) for b in bars]
        vols = [vdate.get(b.date) for b in bars]

        for i in range(window - 1, n - max_h - 1, step):
            sc = scores.get((sym, bars[i].date))
            if sc is None:
                continue
            b = _bucket(sc)
            if b is None:
                continue
            div = compute_divergence(
                closes[: i + 1], inst_arr[: i + 1], vols[: i + 1],
                window=window, price_eps=price_eps, flow_eps=flow_eps, min_points=min_points,
            )
            if div is None:
                continue
            oc = engine.evaluate_signal(BacktestSignal(sym, bars[i].date, sc), bars)
            if oc is None or horizon not in oc.forward.horizons:
                continue
            cell[(b, _col(div.status))].append(oc.forward.horizons[horizon].net_return)

    return cell


def _fmt(cell, horizon, window):
    def avg(b, c):
        v = cell.get((b, c), [])
        return (st.mean(v), len(v)) if v else (None, 0)

    lines = [
        f"\n=== 背離 × Chip Score（window={window}, {horizon}D net avg）===",
        f"{'score':<10}" + "".join(f"{zh:>16}" for _, zh in DIV_COLS) + f"{'正−負':>10}",
    ]
    col_all: dict[str, list[float]] = defaultdict(list)
    for lo, hi in SCORE_BUCKETS:
        b = (lo, hi)
        cells = f"{f'[{lo},{hi})':<10}"
        vals: dict[str, float | None] = {}
        for key, _ in DIV_COLS:
            m, cnt = avg(b, key)
            vals[key] = m
            cells += (f"{m:>10.2%}({cnt:>3})" if m is not None else f"{'-':>16}")
            if m is not None:
                col_all[key].append(m)
        if vals["bullish_div"] is not None and vals["bearish_div"] is not None:
            cells += f"{vals['bullish_div'] - vals['bearish_div']:>+10.2%}"
        lines.append(cells)
    # 欄總計（各背離欄跨所有分數段 pool 平均）
    tot = f"{'全體':<10}"
    pooled: dict[str, float | None] = {}
    for key, _ in DIV_COLS:
        allv = [x for (b, c), lst in cell.items() if c == key for x in lst]
        pooled[key] = st.mean(allv) if allv else None
        tot += (f"{pooled[key]:>10.2%}({len(allv):>4})" if pooled[key] is not None else f"{'-':>16}")
    if pooled["bullish_div"] is not None and pooled["bearish_div"] is not None:
        tot += f"{pooled['bullish_div'] - pooled['bearish_div']:>+10.2%}"
    lines.append(tot)
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=60)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--min-turnover", type=float, default=20_000_000)
    args = ap.parse_args()

    bars, inst, vol, med = await _load()
    scores = await _load_scores()
    cell = run(
        bars, inst, vol, med, scores,
        window=args.window, step=args.step, horizon=args.horizon, min_turnover=args.min_turnover,
    )
    print(_fmt(cell, args.horizon, args.window))


if __name__ == "__main__":
    asyncio.run(main())
