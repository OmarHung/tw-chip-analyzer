"""單調性的正解檢驗:分位數分層 + rank IC(Spearman)。

固定 score bucket 因分數擠在窄區間(多數 50~60、70+ 幾乎為空)而失效,
無法看出單調性。改用每日橫斷面分位:把當日全市場依 chip_score 分 10 組,
看高分組 vs 低分組的未來報酬,並算 Spearman rank IC(score vs forward return)。

成功標準(CLAUDE.md):Chip Score 越高、未來報酬單調改善,且 MAE 不惡化。

用法:APP_ENV=dev python -m scripts.backtest_ic
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd

from app.backtest import BacktestEngine
from app.backtest.runner import load_bars, load_signals
from app.db.session import get_sessionmaker


async def _amain() -> None:
    sm = get_sessionmaker()
    engine = BacktestEngine()
    async with sm() as s:
        signals = await load_signals(s)
        symbols = sorted({sig.symbol for sig in signals})
        prices = await load_bars(s, symbols)

    horizons = engine.horizons
    recs: list[dict] = []
    for sig in signals:
        bars = prices.get(sig.symbol)
        if not bars:
            continue
        oc = engine.evaluate_signal(sig, list(bars))
        if oc is None:
            continue
        row = {"date": sig.data_date, "score": sig.chip_score}
        for h in horizons:
            hr = oc.forward.horizons.get(h)
            row[f"ret{h}"] = hr.net_return if hr else np.nan
            row[f"mae{h}"] = hr.mae if hr else np.nan
        recs.append(row)

    df = pd.DataFrame(recs)
    print(f"評估訊號數={len(df)}  交易日={df['date'].nunique()}  "
          f"score: min={df['score'].min():.1f} avg={df['score'].mean():.1f} max={df['score'].max():.1f}\n")

    # --- Spearman rank IC(逐日橫斷面,再平均;這是因子有效性的標準指標)---
    print("=== 逐日橫斷面 Spearman IC(score vs forward return)===")
    print(f"{'horizon':<9}{'meanIC':>9}{'IC>0比例':>10}{'ICIR':>8}{'t值':>8}")
    for h in horizons:
        ics = []
        for _, g in df.groupby("date"):
            sub = g[["score", f"ret{h}"]].dropna()
            if len(sub) >= 20 and sub["score"].nunique() > 1:
                # spearman = 排名的 pearson(避免 scipy 相依)
                ic = np.corrcoef(sub["score"].rank(), sub[f"ret{h}"].rank())[0, 1]
                if not np.isnan(ic):
                    ics.append(ic)
        ics = np.array(ics)
        if ics.size:
            mean_ic = ics.mean()
            icir = mean_ic / ics.std(ddof=1) if ics.std(ddof=1) > 0 else 0.0
            tval = icir * np.sqrt(ics.size)
            print(f"{h}D{'':<7}{mean_ic:>9.4f}{(ics>0).mean():>9.0%}{icir:>8.2f}{tval:>8.2f}")

    # --- 分位數分層(全樣本 decile)---
    print("\n=== chip_score decile × forward return(net avg)===")
    df["decile"] = pd.qcut(df["score"], 10, labels=False, duplicates="drop") + 1
    hdr = "decile" + "".join(f"{f'{h}D':>10}" for h in horizons) + f"{'n':>8}"
    print(hdr)
    for d, g in df.groupby("decile"):
        cells = "".join(f"{g[f'ret{h}'].mean():>10.2%}" for h in horizons)
        print(f"D{int(d):<5}{cells}{len(g):>8}")

    # --- MAE 是否隨分數惡化(取 5D / 20D)---
    print("\n=== decile × avg MAE(越接近 0 越好)===")
    for h in [5, 20]:
        if h not in horizons:
            continue
        line = " ".join(f"D{int(d)}={g[f'mae{h}'].mean():.2%}" for d, g in df.groupby("decile"))
        print(f"{h}D MAE: {line}")


if __name__ == "__main__":
    asyncio.run(_amain())
