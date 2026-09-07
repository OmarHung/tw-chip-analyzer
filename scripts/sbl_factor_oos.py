"""借券(SBL)因子的樣本外(OOS)檢驗——它是否比融券更能預測 forward return?

背景:memory 記融券 `short_balance_change_z`(軋空)是唯一 OOS-robust 的因子。
借券(SBL)是更大、更聰明錢的做空池,理論上訊號更強。此腳本直接讀 sbl_daily
算借券因子,並拉 feature_daily 的融券因子做**同期同法對照**。

方法(對齊 factor_ic_oos):20D forward net return;train / embargo(20) / test;
逐日橫斷面 rank IC,平均 + t;robust = 方向延續且 |test t|>2。

用法:APP_ENV=dev python -m scripts.sbl_factor_oos
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
HZ = 20


def _day_ic(g: pd.DataFrame, value: pd.Series, ret_col: str) -> float | None:
    sub = pd.DataFrame({"v": value, "r": g[ret_col]}).dropna()
    if len(sub) < 20 or sub["v"].nunique() <= 1:
        return None
    return float(np.corrcoef(sub["v"].rank(), sub["r"].rank())[0, 1])


def _mean_ic(df: pd.DataFrame, dates, col: str, ret_col: str):
    ics = [ic for d in dates
           if (ic := _day_ic(g := df[df["data_date"] == d], g[col], ret_col)) is not None]
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
        sbl = pd.DataFrame(
            (await s.execute(text(
                "select symbol, data_date, sbl_balance, sbl_short_sell, sbl_return "
                "from sbl_daily where data_date >= '2026-04-01' order by symbol, data_date"
            ))).all(),
            columns=["symbol", "data_date", "sbl_balance", "sbl_short_sell", "sbl_return"],
        )
        # 融券對照因子
        fin = pd.DataFrame(
            (await s.execute(text(
                "select symbol, data_date, short_balance_change_z from feature_daily "
                "where data_date >= '2026-04-01'"
            ))).all(),
            columns=["symbol", "data_date", "short_balance_change_z"],
        )
        prices = await load_bars(s, sorted(sbl["symbol"].unique()))

    if sbl.empty:
        print("sbl_daily 無資料——先回填。")
        return

    for c in ["sbl_balance", "sbl_short_sell", "sbl_return"]:
        sbl[c] = pd.to_numeric(sbl[c], errors="coerce")

    # 每檔時序衍生因子
    sbl = sbl.sort_values(["symbol", "data_date"])
    g = sbl.groupby("symbol", group_keys=False)
    sbl["sbl_bal_chg_5d"] = g["sbl_balance"].transform(lambda x: x - x.shift(5))
    sbl["sbl_bal_chg_20d"] = g["sbl_balance"].transform(lambda x: x - x.shift(20))
    sbl["sbl_bal_pct_5d"] = g["sbl_balance"].transform(lambda x: (x - x.shift(5)) / x.shift(5).replace(0, np.nan))
    sbl["sbl_bal_pct_20d"] = g["sbl_balance"].transform(lambda x: (x - x.shift(20)) / x.shift(20).replace(0, np.nan))
    sbl["sbl_net_flow_5d"] = g.apply(
        lambda d: (d["sbl_short_sell"] - d["sbl_return"]).rolling(5).sum()
    ).reset_index(level=0, drop=True)

    df = sbl.merge(fin, on=["symbol", "data_date"], how="left")

    SBL_FACTORS = [
        "sbl_bal_chg_5d", "sbl_bal_chg_20d",
        "sbl_bal_pct_5d", "sbl_bal_pct_20d",
        "sbl_net_flow_5d",
    ]
    BENCH = ["short_balance_change_z"]  # 融券對照

    # forward 20D net return per row
    rets = []
    for sym, d in zip(df["symbol"], df["data_date"]):
        bars = prices.get(sym)
        oc = engine.evaluate_signal(BacktestSignal(sym, d, 0.0), list(bars)) if bars else None
        hr = oc.forward.horizons.get(HZ) if oc else None
        rets.append(hr.net_return if hr else np.nan)
    df[f"ret{HZ}"] = rets
    ret_col = f"ret{HZ}"

    days = sorted(df["data_date"].unique())
    n = len(days)
    split = n // 2
    train_days = days[: split - EMBARGO // 2]
    test_days = days[split + EMBARGO // 2:]
    print(f"SBL 交易日 {n}  train={len(train_days)}  embargo={EMBARGO}  test={len(test_days)}\n")

    print(f"=== 借券 vs 融券 單因子 {HZ}D IC:train vs test ===")
    print(f"{'factor':<24}{'train IC':>11}{'test IC':>11}{'test t':>9}{'n':>6}  robust")
    for f in SBL_FACTORS + BENCH:
        tr, _, _ = _mean_ic(df, train_days, f, ret_col)
        te, te_t, ntest = _mean_ic(df, test_days, f, ret_col)
        robust = "✓" if (tr is not None and te is not None
                         and np.sign(tr) == np.sign(te) and abs(te_t or 0) > 2) else ""
        tag = "  [融券對照]" if f in BENCH else ""
        print(f"{f:<24}{(tr or 0):>+11.4f}{(te or 0):>+11.4f}{(te_t or 0):>9.2f}{ntest:>6}  {robust}{tag}")


if __name__ == "__main__":
    asyncio.run(_amain())
