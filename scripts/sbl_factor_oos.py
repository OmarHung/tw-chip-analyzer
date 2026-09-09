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
from app.backtest.metrics import newey_west_t
from app.backtest.runner import load_bars
from app.db.session import get_sessionmaker

EMBARGO = 20
HZ = 20
# 樣本起點:取全部已回補的 SBL 歷史(缺漏日見 scripts/backfill_sbl.py)。有效 test
# 橫斷面日 ≈ N/2 − EMBARGO/2 − HZ,故 N 越大結論才越站得住(N≈120 才有 ~30 天)。
START = "2026-01-01"


def _day_ic(g: pd.DataFrame, value: pd.Series, ret_col: str) -> float | None:
    sub = pd.DataFrame({"v": value, "r": g[ret_col]}).dropna()
    if len(sub) < 20 or sub["v"].nunique() <= 1:
        return None
    return float(np.corrcoef(sub["v"].rank(), sub["r"].rank())[0, 1])


def _mean_ic(df: pd.DataFrame, dates, col: str, ret_col: str):
    """回傳 (平均 IC, 樸素 t, Newey-West t, 有效日數)。

    樸素 t 把 n 個重疊日當獨立樣本、必然高估；NW t 以 HZ-1 階 Bartlett 權重修正
    自相關，是判斷是否 robust 的依據（樸素 t 只留作對照，看膨脹了多少）。
    """
    ics = [ic for d in dates
           if (ic := _day_ic(g := df[df["data_date"] == d], g[col], ret_col)) is not None]
    a = np.array(ics)
    if a.size < 2:
        return None, None, None, 0
    std = a.std(ddof=1)
    t = (a.mean() / std) * np.sqrt(a.size) if std > 0 else 0.0
    return a.mean(), t, newey_west_t(a, lags=HZ - 1), a.size


async def _amain() -> None:
    sm = get_sessionmaker()
    engine = BacktestEngine()
    async with sm() as s:
        sbl = pd.DataFrame(
            (await s.execute(text(
                f"select symbol, data_date, sbl_balance, sbl_short_sell, sbl_return "
                f"from sbl_daily where data_date >= '{START}' order by symbol, data_date"
            ))).all(),
            columns=["symbol", "data_date", "sbl_balance", "sbl_short_sell", "sbl_return"],
        )
        # 融券對照因子
        fin = pd.DataFrame(
            (await s.execute(text(
                f"select symbol, data_date, short_balance_change_z from feature_daily "
                f"where data_date >= '{START}'"
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
    print(f"{'factor':<24}{'train IC':>11}{'test IC':>11}{'樸素 t':>9}{'NW t':>8}{'n':>5}  robust")
    for f in SBL_FACTORS + BENCH:
        tr, _, _, _ = _mean_ic(df, train_days, f, ret_col)
        te, te_t, te_nw, ntest = _mean_ic(df, test_days, f, ret_col)
        # robust 以 NW t 判定(樸素 t 因重疊視窗必然膨脹,不可作為門檻)
        robust = "✓" if (tr is not None and te is not None
                         and np.sign(tr) == np.sign(te) and abs(te_nw or 0) > 2) else ""
        tag = "  [融券對照]" if f in BENCH else ""
        print(f"{f:<24}{(tr or 0):>+11.4f}{(te or 0):>+11.4f}"
              f"{(te_t or 0):>9.2f}{(te_nw or 0):>8.2f}{ntest:>5}  {robust}{tag}")

    # regime 分層:借券(做空)因子在多頭與非多頭很可能方向不同,混算會互相抵消。
    # 以 market_daily.market_trend_score 切;目前樣本若全在同一 regime,另一層會空。
    async with sm() as s2:
        md = pd.DataFrame(
            (await s2.execute(text(
                "select data_date, market_trend_score from market_daily"
            ))).all(),
            columns=["data_date", "market_trend_score"],
        )
    md["market_trend_score"] = pd.to_numeric(md["market_trend_score"], errors="coerce")
    bull = set(md.loc[md["market_trend_score"] > 0.3, "data_date"])
    print(f"\n=== regime 分層(全樣本,非 OOS;多頭日={len(bull)}/{n}) ===")
    print(f"{'factor':<24}{'多頭 IC':>10}{'NW t':>8}{'n':>5}{'非多頭 IC':>12}{'NW t':>8}{'n':>5}")
    for f in SBL_FACTORS + BENCH:
        b_ic, _, b_nw, b_n = _mean_ic(df, [d for d in days if d in bull], f, ret_col)
        o_ic, _, o_nw, o_n = _mean_ic(df, [d for d in days if d not in bull], f, ret_col)
        print(f"{f:<24}{(b_ic or 0):>+10.4f}{(b_nw or 0):>8.2f}{b_n:>5}"
              f"{(o_ic or 0):>+12.4f}{(o_nw or 0):>8.2f}{o_n:>5}")


if __name__ == "__main__":
    asyncio.run(_amain())
