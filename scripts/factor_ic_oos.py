"""institutional 因子的樣本外(OOS)驗證:train 段定方向/權重,test 段驗證。

回答「照 in-sample IC 翻方向」是真改善還是 regime 幻覺。
時間分割:train 段(前)/ embargo(20 交易日,防 forward return 洩漏)/ test 段(後)。

比較三種 institutional 權重方案在 test 段的 IC:
- config:現行 config/thresholds.yaml 的 weights.institutional
- sign(train IC):用 train 段各因子 IC 的方向、等權
- train IC 加權:用 train 段 IC 值當權重(含方向與強度)

單因子並列 train IC vs test IC(方向是否延續 = 是否 robust)。

用法:APP_ENV=dev python -m scripts.factor_ic_oos
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

FACTORS = [
    "foreign_5d_z", "trust_5d_z", "dealer_5d_z",
    "margin_balance_change_z", "short_balance_change_z",
]
# 現行 config weights.institutional(sbl 全 0 略)
CONFIG_W = {
    "foreign_5d_z": 0.25, "trust_5d_z": 0.30, "dealer_5d_z": 0.10,
    "margin_balance_change_z": -0.15, "short_balance_change_z": -0.10,
}
EMBARGO = 20  # 交易日;>= 最長 horizon,防 train forward return 洩漏進 test
HZ = 20       # 定號與評估主 horizon(訊號最強、與持有期一致)


def _day_ic(g: pd.DataFrame, value: pd.Series, ret_col: str) -> float | None:
    sub = pd.DataFrame({"v": value, "r": g[ret_col]}).dropna()
    if len(sub) < 20 or sub["v"].nunique() <= 1:
        return None
    return float(np.corrcoef(sub["v"].rank(), sub["r"].rank())[0, 1])


def _mean_ic(df: pd.DataFrame, dates, value_fn, ret_col: str):
    ics = [ic for d in dates
           if (ic := _day_ic(g := df[df["data_date"] == d], value_fn(g), ret_col)) is not None]
    a = np.array(ics)
    if a.size < 2:
        return None, None
    std = a.std(ddof=1)
    t = (a.mean() / std) * np.sqrt(a.size) if std > 0 else 0.0
    return a.mean(), t


async def _amain() -> None:
    sm = get_sessionmaker()
    engine = BacktestEngine()
    async with sm() as s:
        df = pd.DataFrame(
            (await s.execute(text(
                f"select symbol, data_date, {', '.join(FACTORS)} from feature_daily "
                "where data_date >= '2026-04-01' order by data_date"
            ))).all(),
            columns=["symbol", "data_date", *FACTORS],
        )
        prices = await load_bars(s, sorted(df["symbol"].unique()))

    for c in FACTORS:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

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
    train_days = days[:split - EMBARGO // 2]
    test_days = days[split + EMBARGO // 2:]
    print(f"交易日 {n} 天  train={len(train_days)}  embargo={EMBARGO}  test={len(test_days)}\n")

    # --- 單因子 train IC vs test IC ---
    print(f"=== 單因子 {HZ}D IC:train vs test(方向延續 = robust)===")
    print(f"{'factor':<28}{'train IC':>11}{'test IC':>11}{'test t':>9}  robust")
    train_ic = {}
    for f in FACTORS:
        tr, _ = _mean_ic(df, train_days, lambda g, f=f: g[f], ret_col)
        te, te_t = _mean_ic(df, test_days, lambda g, f=f: g[f], ret_col)
        train_ic[f] = tr or 0.0
        robust = "✓" if (tr is not None and te is not None
                         and np.sign(tr) == np.sign(te) and abs(te_t) > 2) else ""
        print(f"{f:<28}{(tr or 0):>+11.4f}{(te or 0):>+11.4f}{(te_t or 0):>9.2f}  {robust}")

    # --- 三種權重方案在 test 段的 combo IC ---
    def combo(weights):
        return lambda g: sum(w * g[f] for f, w in weights.items())

    sign_w = {f: float(np.sign(ic)) for f, ic in train_ic.items()}
    icw = dict(train_ic)

    print(f"\n=== institutional 組合方案在 test 段 {HZ}D IC ===")
    print(f"{'方案':<22}{'test IC':>11}{'test t':>9}")
    for label, w in [
        ("config 現行", CONFIG_W),
        ("sign(train IC) 等權", sign_w),
        ("train IC 加權", icw),
    ]:
        ic, t = _mean_ic(df, test_days, combo(w), ret_col)
        print(f"{label:<22}{(ic or 0):>+11.4f}{(t or 0):>9.2f}")
    print("\n權重:")
    print(f"  config          {CONFIG_W}")
    print(f"  sign(train IC)  {sign_w}")
    print(f"  train IC 加權    { {k: round(v,4) for k,v in icw.items()} }")


if __name__ == "__main__":
    asyncio.run(_amain())
