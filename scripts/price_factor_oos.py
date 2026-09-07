"""價格類因子的樣本外(OOS)檢驗——動能 / 波動率 / 乖離是否對 forward return 有增量?

相對 SBL:價格資料完整無缺口、不被 TWSE throttle,故 test 段樣本充足、可解讀。
因子皆 look-ahead 安全(只用 <=t 的收盤;進場為 t 之後第一根 bar,由引擎處理)。

方法對齊 factor_ic_oos:train / embargo(20) / test;逐日橫斷面 rank IC + t;
robust = train/test 方向延續且 |test t|>2。多 horizon(5D/20D)。

用法:APP_ENV=dev python -m scripts.price_factor_oos
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.backtest import BacktestEngine
from app.backtest.engine import BacktestSignal
from app.backtest.runner import load_bars
from app.db.session import get_sessionmaker

EMBARGO = 20
HORIZONS = [5, 20]
FACTORS = [
    "mom_5d", "mom_20d", "mom_60d",   # 動能
    "rev_5d",                          # 短期反轉(= -mom_5d)
    "vol_20d",                         # 已實現波動率(日報酬標準差)
    "close_vs_ma20", "atr_pct",        # 乖離、波動(來自 feature_daily)
]


def _day_ic(g: pd.DataFrame, col: str, ret_col: str) -> float | None:
    sub = g[[col, ret_col]].dropna()
    if len(sub) < 20 or sub[col].nunique() <= 1:
        return None
    return float(np.corrcoef(sub[col].rank(), sub[ret_col].rank())[0, 1])


def _mean_ic(df: pd.DataFrame, dates, col: str, ret_col: str):
    ics = [ic for d in dates
           if (ic := _day_ic(df[df["data_date"] == d], col, ret_col)) is not None]
    a = np.array(ics)
    if a.size < 2:
        return None, None, 0
    std = a.std(ddof=1)
    t = (a.mean() / std) * np.sqrt(a.size) if std > 0 else 0.0
    return a.mean(), t, a.size


async def _amain() -> None:
    sm = get_sessionmaker()
    engine = BacktestEngine()
    async with sm() as s:
        symbols = [r[0] for r in (await s.execute(text(
            "select distinct symbol from daily_price"))).all()]
        prices = await load_bars(s, symbols)
        feat = pd.DataFrame(
            (await s.execute(text(
                "select symbol, data_date, close_vs_ma20_pct, atr14, close "
                "from feature_daily where data_date >= '2026-04-01'"
            ))).all(),
            columns=["symbol", "data_date", "close_vs_ma20", "atr14", "fclose"],
        )

    # 由收盤序列建每檔因子(long DataFrame)
    rows = []
    for sym, bars in prices.items():
        if len(bars) < 61:
            continue
        c = pd.Series([b.close for b in bars], dtype=float)
        dates = [b.date for b in bars]
        ret1 = c.pct_change()
        mom5 = c / c.shift(5) - 1
        mom20 = c / c.shift(20) - 1
        mom60 = c / c.shift(60) - 1
        vol20 = ret1.rolling(20).std()
        for i in range(len(bars)):
            rows.append({
                "symbol": sym, "data_date": dates[i],
                "mom_5d": mom5.iloc[i], "mom_20d": mom20.iloc[i], "mom_60d": mom60.iloc[i],
                "rev_5d": -mom5.iloc[i], "vol_20d": vol20.iloc[i],
            })
    df = pd.DataFrame(rows)
    df = df.merge(feat, on=["symbol", "data_date"], how="left")
    df["atr_pct"] = pd.to_numeric(df["atr14"], errors="coerce") / pd.to_numeric(df["fclose"], errors="coerce")
    df["close_vs_ma20"] = pd.to_numeric(df["close_vs_ma20"], errors="coerce")
    df = df[df["data_date"] >= pd.Timestamp("2026-04-01").date()]

    # forward net return per horizon
    for hz in HORIZONS:
        rets = []
        for sym, d in zip(df["symbol"], df["data_date"]):
            bars = prices.get(sym)
            oc = engine.evaluate_signal(BacktestSignal(sym, d, 0.0), list(bars)) if bars else None
            hr = oc.forward.horizons.get(hz) if oc else None
            rets.append(hr.net_return if hr else np.nan)
        df[f"ret{hz}"] = rets

    days = sorted(df["data_date"].unique())
    n = len(days)
    split = n // 2
    train_days = days[: split - EMBARGO // 2]
    test_days = days[split + EMBARGO // 2:]
    print(f"交易日 {n}  train={len(train_days)}  embargo={EMBARGO}  test={len(test_days)}")

    for hz in HORIZONS:
        ret_col = f"ret{hz}"
        print(f"\n=== 價格因子 {hz}D IC:train vs test(robust = 方向延續且 |test t|>2)===")
        print(f"{'factor':<16}{'train IC':>11}{'test IC':>11}{'test t':>9}{'days':>6}  robust")
        for f in FACTORS:
            tr, _, _ = _mean_ic(df, train_days, f, ret_col)
            te, te_t, nd = _mean_ic(df, test_days, f, ret_col)
            robust = "✓" if (tr is not None and te is not None
                             and np.sign(tr) == np.sign(te) and abs(te_t or 0) > 2) else ""
            print(f"{f:<16}{(tr or 0):>+11.4f}{(te or 0):>+11.4f}{(te_t or 0):>9.2f}{nd:>6}  {robust}")


if __name__ == "__main__":
    asyncio.run(_amain())
