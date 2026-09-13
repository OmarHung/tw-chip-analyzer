"""Phase 2 MOPS shadow 因子的單因子 OOS 驗證（docs/14 §7）。**只輸出報告，不改任何權重。**

方法（與 Phase 1 驗證同口徑）：
- 進場＝訊號日之後的**市場**次一交易日 open（BacktestEngine + market_calendar），報酬含 backtest.costs。
- 時序切分：train = 前半；test = 後半並跳過 embargo = max(backtest.horizons) 個交易日。
- 逐日橫斷面 Spearman rank IC（當日樣本 < validation.min_ic_names 不計）；平均 IC、樸素 t、
  Newey–West t（lag = horizon − 1；重疊報酬會讓樸素 t 高估）。
- regime（全樣本、非 OOS）：market_trend_score > mops.research.bull_min_trend 分多頭/非多頭。
- coverage / 缺漏率 / holding source 組成 / survivorship（有特徵、日期夠舊卻無後續 bar 的筆數）。
- ablation：Phase 1 chip_score 的 IC，與因子對 chip_score 當日排名殘差化後的增量 IC。

Provenance 分段（docs/14 §7.1）：
- **retrospective research**：全部資料（含 backfill / mixed / unknown），只列統計，**永不標 robust**。
- **honest forward OOS**：只收該因子 provenance = point_in_time_safe 的列；在這些日子上重新切
  train/embargo/test。test 有效橫斷面日 < mops.research.min_honest_test_days → 「樣本不足，不可判斷」，
  不因 retrospective 顯著而標 robust。

即使結果看似顯著，也只能作為「持續累積 OOS」的建議；啟用權重不在本腳本範圍。

用法：APP_ENV=dev python -m scripts.mops_factor_oos
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.backtest import BacktestEngine
from app.backtest.engine import BacktestSignal, market_calendar
from app.backtest.metrics import newey_west_t
from app.backtest.runner import load_bars
from app.core.config import get_thresholds
from app.db.models.features import SignalSnapshot
from app.db.models.market import MarketDaily
from app.db.models.mops import MopsShadowFeatureDaily
from app.db.session import get_sessionmaker
from app.services.mops_features import (
    PROV_BACKFILL,
    PROV_SAFE,
    RAW_TO_Z,
    provenance_column,
)

FACTORS = list(RAW_TO_Z)
BASELINE = "chip_score"
PROVENANCE_COLS = ["holding_provenance", "transfer_provenance"]
VERDICT_RETRO = "retrospective（不判定）"
VERDICT_INSUFFICIENT = "樣本不足，不可判斷"
VERDICT_ROBUST = "robust ✓"
VERDICT_FAIL = "未通過"


# ---------------------------------------------------------------- 純函式（有測試）

def split_days(days: Sequence, embargo: int) -> tuple[list, list]:
    """前半 train、後半 test；兩段之間隔 embargo 個交易日，避免 forward 報酬重疊洩漏。"""
    days = sorted(days)
    mid = len(days) // 2
    return list(days[:mid]), list(days[mid + embargo:])


def day_ic(values: pd.Series, returns: pd.Series, min_names: int) -> float | None:
    sub = pd.DataFrame({"v": values, "r": returns}).dropna()
    if len(sub) < min_names or sub["v"].nunique() <= 1 or sub["r"].nunique() <= 1:
        return None
    return float(sub["v"].rank().corr(sub["r"].rank()))


def ic_stats(ics: Sequence[float], horizon: int) -> dict:
    a = np.asarray([x for x in ics if x is not None], dtype=float)
    if a.size < 2:
        return {"ic": None, "t_naive": None, "t_nw": None, "n_days": int(a.size)}
    std = a.std(ddof=1)
    return {
        "ic": float(a.mean()),
        "t_naive": float(a.mean() / std * np.sqrt(a.size)) if std > 0 else 0.0,
        "t_nw": newey_west_t(a, lags=max(horizon - 1, 0)),
        "n_days": int(a.size),
    }


def residualize(factor: pd.Series, baseline: pd.Series) -> pd.Series:
    """當日因子排名對 baseline 排名做 OLS，回傳殘差（兩者皆有值的列才有結果）。"""
    sub = pd.DataFrame({"f": factor, "b": baseline}).dropna()
    out = pd.Series(np.nan, index=factor.index)
    if len(sub) < 3 or sub["b"].nunique() <= 1:
        return out
    fr, br = sub["f"].rank(), sub["b"].rank()
    slope = np.cov(fr, br, ddof=0)[0, 1] / np.var(br)
    out.loc[sub.index] = fr - (fr.mean() + slope * (br - br.mean()))
    return out


def honest_values(df: pd.DataFrame, factor: str) -> pd.Series:
    """只保留該因子 provenance = point_in_time_safe 的值；backfill / mixed / unknown → NaN。"""
    return df[factor].where(df[provenance_column(factor)] == PROV_SAFE)


def provenance_counts(df: pd.DataFrame, factor: str, min_names: int) -> dict:
    """該因子非 NULL 樣本依 provenance 分組計數，與 honest 有效橫斷面日數。"""
    has = df[df[factor].notna()]
    prov = has[provenance_column(factor)]
    safe = has[prov == PROV_SAFE]
    per_day = safe.groupby("data_date").size()
    return {
        "safe": int((prov == PROV_SAFE).sum()),
        "backfill": int((prov == PROV_BACKFILL).sum()),
        "mixed_unknown": int((~prov.isin([PROV_SAFE, PROV_BACKFILL])).sum()),
        "honest_days": int((per_day >= min_names).sum()),
    }


def verdict(tr: dict, te: dict, *, honest: bool, min_test_days: int) -> str:
    """robust 只可能出現在 honest OOS，且 test 有效日數達門檻、方向一致、|test NW t| > 2。"""
    if not honest:
        return VERDICT_RETRO
    if tr["ic"] is None or te["ic"] is None or te["n_days"] < min_test_days:
        return VERDICT_INSUFFICIENT
    same = np.sign(tr["ic"]) == np.sign(te["ic"])
    return VERDICT_ROBUST if same and abs(te["t_nw"] or 0) > 2 else VERDICT_FAIL


def daily_ics(df: pd.DataFrame | dict, days: Sequence, col: str, ret_col: str, min_names: int,
              resid_on: str | None = None) -> list[float]:
    """df 可傳 {data_date: 當日子表}（預先分組，避免每個因子/horizon 反覆全表過濾）。"""
    by_day = df if isinstance(df, dict) else {d: g for d, g in df.groupby("data_date")}
    out = []
    for d in days:
        g = by_day.get(d)
        if g is None:
            continue
        vals = residualize(g[col], g[resid_on]) if resid_on else g[col]
        ic = day_ic(vals, g[ret_col], min_names)
        if ic is not None:
            out.append(ic)
    return out


# ---------------------------------------------------------------- 報告

def _fmt(x, spec="+.4f") -> str:
    return "—" if x is None else format(x, spec)


async def _load() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    async with get_sessionmaker()() as s:
        cols = ["symbol", "data_date", "holding_source", *PROVENANCE_COLS, *FACTORS]
        feats = pd.DataFrame(
            (await s.execute(select(*[getattr(MopsShadowFeatureDaily, c) for c in cols]))).all(),
            columns=cols,
        )
        base = pd.DataFrame(
            (await s.execute(select(SignalSnapshot.symbol, SignalSnapshot.data_date,
                                    SignalSnapshot.chip_score))).all(),
            columns=["symbol", "data_date", BASELINE],
        )
        md = pd.DataFrame(
            (await s.execute(select(MarketDaily.data_date, MarketDaily.market_trend_score))).all(),
            columns=["data_date", "market_trend_score"],
        )
        prices = await load_bars(s, sorted(feats["symbol"].unique())) if not feats.empty else {}
    # 價格不可放 df.attrs：pandas 每次過濾都會 deep-copy attrs，整包 bars 會讓報告慢上百倍
    return feats.merge(base, on=["symbol", "data_date"], how="left"), md, prices


async def _amain() -> None:
    t = get_thresholds()
    horizons = sorted(t.backtest.get("horizons", [1, 3, 5, 10, 20]))
    embargo = max(horizons)
    min_names = int(t.get("validation", "min_ic_names", default=20))
    bull_min = float(t.get("mops", "research", "bull_min_trend", default=0.3))

    df, md, prices = await _load()
    if df.empty:
        print("mops_shadow_feature_daily 無資料——先跑 app.jobs.mops / 回補腳本 / rebuild_mops_features。")
        return
    for c in [*FACTORS, BASELINE]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    calendar = market_calendar(prices)
    engine = BacktestEngine()
    rets = {h: [] for h in horizons}
    for sym, d in zip(df["symbol"], df["data_date"]):
        bars = prices.get(sym)
        oc = engine.evaluate_signal(BacktestSignal(sym, d, 0.0), list(bars), calendar) if bars else None
        for h in horizons:
            hr = oc.forward.horizons.get(h) if oc else None
            rets[h].append(hr.net_return if hr else np.nan)
    for h in horizons:
        df[f"ret{h}"] = rets[h]

    days = sorted(df["data_date"].unique())
    train, test = split_days(days, embargo)
    by_day = {d: g for d, g in df.groupby("data_date")}
    print("Phase 2 MOPS shadow 因子 OOS（只輸出報告，不改權重；Phase 1 §28 仍未達成）")
    print(f"交易日 {len(days)}：train={len(train)}  embargo={embargo}  test={len(test)}  "
          f"每日最低樣本={min_names}  成本={t.backtest.get('costs')}\n")

    # coverage / survivorship
    print("=== coverage ===")
    per_day = df.groupby("data_date")
    for f in FACTORS:
        names = per_day[f].count()
        print(f"{f:<30} 非 NULL {df[f].notna().mean():>6.1%}  缺漏 {df[f].isna().mean():>6.1%}  "
              f"每日檔數 中位 {int(names.median())} / 最少 {int(names.min())}  "
              f"達最低樣本日 {int((names >= min_names).sum())}/{len(days)}")
    print(f"holding source 組成：{df['holding_source'].value_counts(dropna=False).to_dict()}")
    cutoff = calendar[-(embargo + 1)] if len(calendar) > embargo else None
    old = df[df["data_date"] <= cutoff] if cutoff else df.iloc[0:0]
    lost = int(old[f"ret{embargo}"].isna().sum())
    print(f"survivorship：日期夠舊（≤{cutoff}）卻無 {embargo}D 後續 bar 被丟棄 {lost}/{len(old)} 筆"
          f"（停牌/下市；IC 只看存活者，可能高估）\n")

    bull = set(md.loc[pd.to_numeric(md["market_trend_score"], errors="coerce") > bull_min, "data_date"])
    min_test_days = int(t.get("mops", "research", "min_honest_test_days", default=20))

    print("=== provenance（非 NULL 樣本數；honest 有效日 = point_in_time_safe 樣本 ≥ 每日最低樣本的日數）===")
    for f in FACTORS:
        c = provenance_counts(df, f, min_names)
        print(f"{f:<30} point_in_time_safe {c['safe']:>7}  backfill {c['backfill']:>7}  "
              f"mixed/unknown {c['mixed_unknown']:>7}  honest 有效日 {c['honest_days']:>4}")
    print()

    honest_df = df.copy()
    for f in FACTORS:
        honest_df[f] = honest_values(df, f)
    honest_by_day = {d: g for d, g in honest_df.groupby("data_date")}

    for h in horizons:
        rc = f"ret{h}"
        _report_section(f"{h}D retrospective research（全部資料，含 backfill/mixed/unknown；不判定 robust）",
                        by_day, train, test, rc, h, min_names, honest=False, min_test_days=min_test_days,
                        with_baseline=True)
        for f in FACTORS:
            h_days = sorted(d for d, g in honest_by_day.items() if g[f].notna().sum() >= min_names)
            h_train, h_test = split_days(h_days, embargo)
            _report_section(
                f"{h}D honest forward OOS：{f}（point_in_time_safe 有效日 {len(h_days)}："
                f"train={len(h_train)} embargo={embargo} test={len(h_test)}；test < {min_test_days} 日不可判斷）",
                honest_by_day, h_train, h_test, rc, h, min_names, honest=True,
                min_test_days=min_test_days, factors=[f],
            )
        print(f"--- {h}D regime（retrospective 全樣本，非 OOS）---")
        for f in FACTORS:
            b = ic_stats(daily_ics(by_day, [d for d in days if d in bull], f, rc, min_names), h)
            o = ic_stats(daily_ics(by_day, [d for d in days if d not in bull], f, rc, min_names), h)
            same = ("一致" if b["ic"] is not None and o["ic"] is not None
                    and np.sign(b["ic"]) == np.sign(o["ic"]) else "不一致/不足")
            print(f"{f:<30} 多頭 {_fmt(b['ic'])} (NW {_fmt(b['t_nw'], '.2f')}, n={b['n_days']})  "
                  f"非多頭 {_fmt(o['ic'])} (NW {_fmt(o['t_nw'], '.2f')}, n={o['n_days']})  {same}")
        print()


def _report_section(title: str, by_day: dict, train: list, test: list, rc: str, h: int,
                    min_names: int, *, honest: bool, min_test_days: int,
                    factors: list[str] | None = None, with_baseline: bool = False) -> None:
    print(f"=== {title} ===")
    print(f"{'factor':<30}{'train IC':>10}{'test IC':>10}{'樸素 t':>9}{'NW t':>8}{'n':>5}"
          f"{'增量 test IC':>14}{'NW t':>8}  判定")
    for f in [*(factors or FACTORS), *([BASELINE] if with_baseline else [])]:
        tr = ic_stats(daily_ics(by_day, train, f, rc, min_names), h)
        te = ic_stats(daily_ics(by_day, test, f, rc, min_names), h)
        inc = (ic_stats(daily_ics(by_day, test, f, rc, min_names, resid_on=BASELINE), h)
               if f != BASELINE else {"ic": None, "t_nw": None})
        tag = "[Phase 1 baseline]" if f == BASELINE else verdict(
            tr, te, honest=honest, min_test_days=min_test_days)
        print(f"{f:<30}{_fmt(tr['ic']):>10}{_fmt(te['ic']):>10}{_fmt(te['t_naive'], '.2f'):>9}"
              f"{_fmt(te['t_nw'], '.2f'):>8}{te['n_days']:>5}{_fmt(inc['ic']):>14}"
              f"{_fmt(inc['t_nw'], '.2f'):>8}  {tag}")
    print()


if __name__ == "__main__":
    asyncio.run(_amain())
