"""法人淨買超佔量比 指標回測：單日 vs 累積(5/20/60日) 哪個對 forward return 有單調 alpha？

用法：
    APP_ENV=dev python -m scripts.flow_ratio_backtest [--step 5] [--min-turnover 2e7]

指標定義（皆 dimensionless，跨股票可比，鐵則6）：
- d1     單日   = 三大法人淨買超[D] / 當日成交量[D]
- cumN   N日累積 = Σ淨買超[D-N+1..D] / Σ成交量[D-N+1..D]
（單位皆張；買超為正）

方法：look-ahead 安全——指標用 data_date<=D 資料算,進場取 >D 次日 open(engine),
net 含成本。每個指標把訊號值 pool 後切五分位,列各 horizon 的 n/win%/avg/median,
看 Q5(最高佔量比)−Q1(最低) 是否為正且單調。鐵則9:未有此證據前不得聲稱可交易。
"""
from __future__ import annotations

import argparse
import asyncio
import statistics as st
from collections import defaultdict

from app.backtest.engine import BacktestEngine, BacktestSignal
from scripts.divergence_backtest import _load

DEFS = {"d1": 1, "cum5": 5, "cum20": 20, "cum60": 60}
Q = 5  # 五分位


def _ratio(inst: list[float | None], vol: list[float | None], i: int, n: int) -> float | None:
    lo = i - n + 1
    if lo < 0:
        return None
    num = sum(x for x in inst[lo : i + 1] if x is not None)
    den = sum(v for v in vol[lo : i + 1] if v is not None)
    if den <= 0:
        return None
    return num / den


def run_study(bars_by_sym, inst_by_sym, vol_by_sym, med_turnover, *, step, min_turnover, max_symbols):
    engine = BacktestEngine()
    horizons = engine.horizons
    max_h = max(horizons)
    max_win = max(DEFS.values())

    # def -> list[(value, {h: net_return})]
    samples: dict[str, list[tuple[float, dict[int, float]]]] = defaultdict(list)

    syms = sorted(bars_by_sym)
    if max_symbols:
        syms = syms[:max_symbols]
    for sym in syms:
        if med_turnover.get(sym, 0.0) < min_turnover:
            continue
        bars = bars_by_sym[sym]
        n = len(bars)
        if n < max_win + max_h + 1:
            continue
        idate = inst_by_sym.get(sym, {})
        vdate = vol_by_sym.get(sym, {})
        inst = [idate.get(b.date) for b in bars]
        vol = [vdate.get(b.date) for b in bars]

        for i in range(max_win - 1, n - max_h - 1, step):
            fwd = None
            for name, w in DEFS.items():
                r = _ratio(inst, vol, i, w)
                if r is None:
                    continue
                if fwd is None:
                    oc = engine.evaluate_signal(BacktestSignal(sym, bars[i].date, 0.0), bars)
                    if oc is None:
                        break
                    fwd = {h: hr.net_return for h, hr in oc.forward.horizons.items()}
                samples[name].append((r, fwd))
    return {"horizons": horizons, "samples": samples}


def _quintile_table(name: str, rows: list[tuple[float, dict[int, float]]], horizons) -> str:
    rows = sorted(rows, key=lambda x: x[0])
    m = len(rows)
    lines = [f"\n=== {name}  法人淨買超佔量比 五分位 × forward return（net, n={m}）==="]
    if m < Q * 5:
        return lines[0] + f"\n  樣本不足({m})"
    edges = [rows[int(m * k / Q)][0] for k in range(1, Q)]
    lines.append("  分位邊界(佔量比): " + " ".join(f"{e:+.3f}" for e in edges))
    buckets = [rows[int(m * k / Q) : int(m * (k + 1) / Q)] for k in range(Q)]
    header = f"{'quintile':<10}{'n':>6}" + "".join(f"{f'{h}D avg':>10}" for h in horizons)
    lines.append(header)
    q_avg: dict[int, list[float]] = {h: [] for h in horizons}
    for qi, b in enumerate(buckets):
        cells = f"{'Q'+str(qi+1):<10}{len(b):>6}"
        for h in horizons:
            vals = [d[h] for _, d in b if h in d]
            avg = st.mean(vals) if vals else 0.0
            q_avg[h].append(avg)
            cells += f"{avg:>9.2%} " if vals else f"{'-':>10}"
        lines.append(cells)
    spread = "  Q5−Q1 價差: " + " ".join(
        f"{h}D {q_avg[h][-1]-q_avg[h][0]:+.2%}" for h in horizons
    )
    lines.append(spread)
    # 單調性：Q1..Q5 avg 是否遞增（以 5D 判定）
    mono_h = 5 if 5 in horizons else horizons[len(horizons) // 2]
    seq = q_avg[mono_h]
    inc = sum(1 for a, b in zip(seq, seq[1:]) if b > a)
    lines.append(f"  {mono_h}D 單調遞增段 {inc}/{Q-1}")
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--min-turnover", type=float, default=20_000_000)
    ap.add_argument("--max-symbols", type=int, default=None)
    args = ap.parse_args()

    bars, inst, vol, med = await _load()
    res = run_study(
        bars, inst, vol, med,
        step=args.step, min_turnover=args.min_turnover, max_symbols=args.max_symbols,
    )
    for name in DEFS:
        print(_quintile_table(name, res["samples"][name], res["horizons"]))


if __name__ == "__main__":
    asyncio.run(main())
