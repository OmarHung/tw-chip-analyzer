"""Phase 2 ML shadow 離線研究腳本（docs/16-phase2-realtime-ml-plan.md §B.1）。

**只做一件事**：檢驗「用既有每日特徵做 ML，測不測得出 rule-based composite 測不出的
訊號」，作為要不要投入 §B.2（honest OOS 框架 + shadow 表）的初步依據。

**本報告是 retrospective / 研究用，不是 honest OOS，不代表可進正式評分路徑。**
不寫任何新表，不碰 `app/services/chip/`、`app/services/analysis.py`、`weights` config；
啟用門檻與流程是 §B.2 的事，本腳本不做（比照 docs/16 §B.4：即使結果看似顯著，也只能
作為「持續累積 OOS」的建議）。

背景：Phase 1 rule-based 訊號本身 §28 仍未達成——各 horizon IC ≈ 0（見 CLAUDE.md
「現況」段落尾端）。SBL / 產業趨勢等既有 shadow 因子的 OOS 先例也大多落在 IC≈0 附近，
一個沒過關（融券對照）。這裡不預設 ML 會不一樣，如實回報就好。

特徵來源：`SignalSnapshot.payload`（`app/services/signal_persist.py::_feature_payload`
產生，已是 look-ahead safe 的每日快照，供回測與未來 ML 而生）。**只取
`config_version` 與目前設定 `data_version(get_thresholds().raw)` 相符的列**——即
「最近一次全量重建」且與現行規則一致的資料，避免訓練樣本跨規則版本（百分位映射改動、
§09 修正等）混用，讓 label 語意不一致（docs/16 §B.4、§D.3 點名的坑）。

Label：forward net return，透過 `BacktestEngine.evaluate_signal`
（= `app/backtest/forward_returns.py` 的封裝）計算，horizon 取
`config/thresholds.yaml: backtest.horizons`，與既有回測/OOS 腳本同口徑。

模型：
- baseline：Ridge（`sklearn.linear_model.Ridge`）——可解釋、與 rule-based 加權合成
  同哲學，訓練快、不易過擬合。
- 次要候選：淺層 GBM（`GradientBoostingRegressor`，max_depth=3、subsample<1、
  早停），**只用於特徵重要性研究**，不是候選 shadow score。
- 明確排除深度學習（MLP/LSTM/Transformer）：有效橫斷面日通常只有幾十天，樣本量撐不住；
  可解釋性差；當 base rate IC≈0 時複雜模型更容易學到噪聲而非訊號（docs/16 §B.3）。

驗證：train（前半）/ embargo(=max(horizons)) / test（後半），逐日橫斷面 rank IC，
樸素 t 與 Newey-West t（複用 `app.backtest.metrics.newey_west_t`；lag=horizon-1）。
train/embargo/test 切分與逐日 IC 聚合直接複用 `scripts/mops_factor_oos.py` 的純函式
（`split_days` / `daily_ics` / `ic_stats`），不重造統計邏輯。train 段的模型預測是
**in-sample**（模型本來就是在這段資料上 fit 的），不是公平的 train/test 對照，只供
參考模型有沒有起碼的擬合能力；唯一有意義的是 test 段（embargo 之後、模型只 predict
不再 fit）。

regime 分層（`market_trend_score > 0.3` 切多頭/非多頭，比照 SBL 先例）只在 test 段做，
避免和 in-sample 的 train 段混在一起稀釋判定。

用法：APP_ENV=dev python -m scripts.ml_shadow_research
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import func, select

from app.backtest import BacktestEngine
from app.backtest.engine import BacktestSignal, market_calendar
from app.backtest.runner import load_bars
from app.core.config import get_thresholds
from app.core.threshold_registry import data_version
from app.db.models.features import SignalSnapshot
from app.db.session import get_sessionmaker
from scripts.mops_factor_oos import daily_ics, ic_stats, split_days

BULL_MIN_TREND = 0.3  # regime 分層門檻，比照 scripts/sbl_factor_oos.py 先例
MIN_TRAIN_ROWS = 200  # train 段可標記筆數低於此視為不可訓練，略過該 horizon

BASELINE_COL = "chip_score"

# 候選特徵：只用已跨股票可比較的正規化欄位（z-score / percentage / -1..1 訊號）。
# 刻意排除 payload 裡的原始價格/量水準（close/ma20/vwap/atr14/recent_swing_low/
# resistance_high/turnover）——CLAUDE.md 鐵則 6「跨股票不可直接比張數」對 ML 特徵
# 同樣適用，混進去等於餵模型不可比較的尺度。也排除純旗標欄位
# （is_limit_locked/market_available）：那是資料可用性註記，不是籌碼/動能訊號。
FEATURE_COLS = [
    "change_pct", "close_vs_ma20_pct", "close_vs_vwap_pct",
    "foreign_5d_z", "trust_5d_z", "dealer_5d_z",
    "margin_balance_change_z", "short_balance_change_z", "sbl_change_z",
    "industry_trend_score",
    "large_holder_ratio_change_z", "retail_holder_ratio_change_z", "holder_count_change_z",
    "cvd_z", "large_trade_delta_z", "cvd_slope_norm", "absorption_z",
    "trade_speed_z", "price_efficiency_z",
    "market_trend_score",
]


# ---------------------------------------------------------------- 純函式（有測試，不碰 DB）

def select_config_version(
    version_counts: dict[str, int], current_version: str
) -> tuple[str, bool]:
    """挑選要用的 config_version。

    優先選與目前設定相符者（=最近一次全量重建、且與現行規則一致，訓練資料的
    label 語意才不會跨版本混用）；若落地資料裡沒有這個版本（例如設定改了但還沒
    重建），退回筆數最多的版本（通常就是最近一次全量重建），並回報 matches_current
    =False，讓呼叫端明確印出警告。
    """
    if not version_counts:
        raise ValueError("signal_snapshot 無資料，先跑 daily job 或 scripts.rebuild_signals")
    if current_version in version_counts:
        return current_version, True
    fallback = max(version_counts, key=lambda v: version_counts[v])
    return fallback, False


def build_feature_frame(rows: Sequence[dict]) -> pd.DataFrame:
    """list of payload-merged dict → DataFrame，特徵/baseline 欄位一律轉數值。

    `pd.to_numeric` 把 bool 轉成 0.0/1.0、None 轉成 NaN；訓練前的缺值處理另由
    模型 pipeline 的 imputer 負責（見 _ridge_pipeline / _gbm_pipeline）。
    """
    df = pd.DataFrame(list(rows))
    if df.empty:
        return df
    for c in [*FEATURE_COLS, BASELINE_COL]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def usable_features(train_fit: pd.DataFrame) -> list[str]:
    """train 段（已篩掉 label 缺失列）裡至少有一筆非空值的特徵——全空的欄位對這個
    horizon/樣本沒有訓練資料，丟給 imputer 只會填出恆定值，不如直接排除更誠實。
    """
    return [c for c in FEATURE_COLS if train_fit[c].notna().any()]


def regime_days(day_trend: pd.Series, bull_min_trend: float) -> tuple[set, set]:
    """依每日大盤趨勢分數切多頭/非多頭日期集合；趨勢未知（NaN，大盤停牌等）的日期
    兩邊都不算，避免誤判成非多頭。
    """
    known = day_trend.dropna()
    bull = set(known[known > bull_min_trend].index)
    non_bull = set(known[known <= bull_min_trend].index)
    return bull, non_bull


def _ridge_pipeline() -> Pipeline:
    """Ridge baseline：中位數補值（研究用簡化——不是正式評分路徑「缺成分就排除」的
    誠實處理，只是為了讓 Ridge 能吃完整矩陣；GBM 版本用原生 NaN 處理，不受此簡化影響）
    + 標準化 + Ridge。alpha 固定不調參——樣本小，不引入巢狀 CV 的額外複雜度（KISS）。
    """
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        Ridge(alpha=1.0, random_state=42),
    )


def _gbm_pipeline() -> Pipeline:
    """淺層 GBM：只用於特徵重要性研究，不是候選 shadow score。
    max_depth=3、subsample=0.8、早停（validation_fraction + n_iter_no_change）
    壓過擬合；仍用 imputer 補值（GradientBoostingRegressor 本身不支援 NaN 輸入，
    與能原生吃 NaN 的 HistGradientBoosting 不同，這裡換 feature_importances_ 的
    可讀性）。
    """
    return make_pipeline(
        SimpleImputer(strategy="median"),
        GradientBoostingRegressor(
            max_depth=3, n_estimators=300, learning_rate=0.05, subsample=0.8,
            validation_fraction=0.15, n_iter_no_change=15, random_state=42,
        ),
    )


# ---------------------------------------------------------------- 報告

def _fmt(x, spec="+.4f") -> str:
    return "—" if x is None else format(x, spec)


async def _load() -> tuple[pd.DataFrame, str, bool, dict[str, int], dict]:
    t = get_thresholds()
    current_version = data_version(t.raw)
    async with get_sessionmaker()() as s:
        counts = (await s.execute(
            select(SignalSnapshot.config_version, func.count())
            .group_by(SignalSnapshot.config_version)
        )).all()
        version_counts = {v: int(n) for v, n in counts}
        version, matches_current = select_config_version(version_counts, current_version)

        stmt = select(
            SignalSnapshot.symbol, SignalSnapshot.data_date,
            SignalSnapshot.chip_score, SignalSnapshot.payload,
        ).where(SignalSnapshot.config_version == version)
        raw_rows = (await s.execute(stmt)).all()
        rows = [
            {**(payload or {}), "symbol": sym, "data_date": d, BASELINE_COL: chip}
            for sym, d, chip, payload in raw_rows
        ]
        symbols = sorted({r["symbol"] for r in rows})
        prices = await load_bars(s, symbols) if symbols else {}
    return build_feature_frame(rows), version, matches_current, version_counts, prices


def _attach_forward_returns(
    df: pd.DataFrame, prices: dict, horizons: list[int]
) -> tuple[pd.DataFrame, list]:
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
    return df, calendar


def _fit_and_score(
    df: pd.DataFrame, train_days: list, test_days: list, h: int, min_names: int,
) -> dict:
    """對單一 horizon：fit Ridge/GBM(train)、predict(train 附帶 in-sample、test)，
    回傳逐模型的 IC 統計 + GBM feature importance + 使用到的特徵清單。

    `h` 同時是 forward return 天數（決定 ret 欄位）與 Newey-West lag（=h-1，
    見 ic_stats/newey_west_t）——兩者必須是同一個 horizon，不可分開傳，否則 NW
    修正的重疊窗長度會跟實際 label 對不上。
    """
    ret_col = f"ret{h}"
    train_df = df[df["data_date"].isin(train_days)].copy()
    test_df = df[df["data_date"].isin(test_days)].copy()
    train_fit = train_df.dropna(subset=[ret_col])
    out: dict = {"n_train_fit": len(train_fit)}
    if len(train_fit) < MIN_TRAIN_ROWS:
        out["skipped"] = f"train 可用列數 {len(train_fit)} < {MIN_TRAIN_ROWS}，略過"
        return out

    cols = usable_features(train_fit)
    out["features_used"] = cols
    scored = pd.concat([train_df, test_df])

    for name, builder in (("ridge", _ridge_pipeline), ("gbm", _gbm_pipeline)):
        model = builder()
        model.fit(train_fit[cols], train_fit[ret_col])
        pred_col = f"pred_{name}"
        scored.loc[train_df.index, pred_col] = model.predict(train_df[cols])
        scored.loc[test_df.index, pred_col] = model.predict(test_df[cols])
        out[name] = {"model": model, "pred_col": pred_col}

    by_day = {d: g for d, g in scored.groupby("data_date")}
    out["by_day"] = by_day
    for name in ("ridge", "gbm"):
        pred_col = out[name]["pred_col"]
        out[name]["train"] = ic_stats(daily_ics(by_day, train_days, pred_col, ret_col, min_names), h)
        out[name]["test"] = ic_stats(daily_ics(by_day, test_days, pred_col, ret_col, min_names), h)
    out["baseline"] = {
        "train": ic_stats(daily_ics(by_day, train_days, BASELINE_COL, ret_col, min_names), h),
        "test": ic_stats(daily_ics(by_day, test_days, BASELINE_COL, ret_col, min_names), h),
    }
    return out


def _print_horizon_report(
    h: int, res: dict, test_days: list, bull_test: list, nonbull_test: list, min_names: int,
) -> None:
    print(f"=== {h}D（retrospective 研究，非 honest OOS）===")
    if "skipped" in res:
        print(f"  {res['skipped']}\n")
        return
    print(f"  train 可用列數={res['n_train_fit']}  使用特徵數={len(res['features_used'])}"
          f"（{len(FEATURE_COLS)} 候選中）")
    print(f"  {'model':<10}{'train IC(in-sample)':>20}{'test IC':>11}{'樸素 t':>9}{'NW t':>8}{'n':>5}  備註")
    rows = [("ridge", res["ridge"]), ("gbm", res["gbm"]), ("chip_score(baseline)", res["baseline"])]
    for label, r in rows:
        tr, te = r["train"], r["test"]
        note = ""
        if te["ic"] is not None and te["t_nw"] is not None:
            same = tr["ic"] is not None and np.sign(tr["ic"]) == np.sign(te["ic"])
            note = "同向且|NW t|>2" if same and abs(te["t_nw"]) > 2 else ""
        print(f"  {label:<10}{_fmt(tr['ic']):>20}{_fmt(te['ic']):>11}"
              f"{_fmt(te['t_naive'], '.2f'):>9}{_fmt(te['t_nw'], '.2f'):>8}{te['n_days']:>5}  {note}")

    by_day = res["by_day"]
    print("  --- test 段 regime 分層（多頭/非多頭 test 日各自的 IC；樣本小則如實顯示 n） ---")
    for name in ("ridge", "gbm"):
        col = res[name]["pred_col"]
        b = ic_stats(daily_ics(by_day, bull_test, col, f"ret{h}", min_names), h)
        o = ic_stats(daily_ics(by_day, nonbull_test, col, f"ret{h}", min_names), h)
        same = ("方向一致" if b["ic"] is not None and o["ic"] is not None
                and np.sign(b["ic"]) == np.sign(o["ic"]) else "不一致/不足")
        print(f"    {name:<8}多頭 {_fmt(b['ic'])} (NW {_fmt(b['t_nw'], '.2f')}, n={b['n_days']})  "
              f"非多頭 {_fmt(o['ic'])} (NW {_fmt(o['t_nw'], '.2f')}, n={o['n_days']})  {same}")

    gbm_model = res["gbm"]["model"].named_steps["gradientboostingregressor"]
    importances = sorted(zip(res["features_used"], gbm_model.feature_importances_),
                          key=lambda x: -x[1])[:8]
    print("  GBM 特徵重要性 top 8（研究用，非權重建議）：")
    for name, imp in importances:
        print(f"    {name:<32}{imp:.4f}")
    print()


async def _amain() -> None:
    t = get_thresholds()
    horizons = sorted(t.backtest.get("horizons", [1, 3, 5, 10, 20]))
    embargo = max(horizons)
    min_names = int(t.get("validation", "min_ic_names", default=20))

    df, version, matches_current, version_counts, prices = await _load()
    print("Phase 2 ML shadow 離線研究（docs/16 §B.1）——retrospective / 研究用，"
          "非 honest OOS，不代表可進正式評分路徑。")
    print(f"signal_snapshot config_version 分布：{version_counts}")
    if matches_current:
        print(f"採用版本 {version}（與目前設定 data_version 一致 = 最近一次全量重建）")
    else:
        print(f"[警告] 目前設定 data_version 在 signal_snapshot 裡沒有對應資料，"
              f"退回筆數最多的版本 {version}（可能不是最新規則；先跑 rebuild_signals 較保險）")
    if df.empty:
        print("該版本下無資料，無法研究。")
        return

    df, calendar = _attach_forward_returns(df, prices, horizons)
    days = sorted(df["data_date"].unique())
    train_days, test_days = split_days(days, embargo)
    day_trend = df.groupby("data_date")["market_trend_score"].median()
    bull_days, nonbull_days = regime_days(day_trend, BULL_MIN_TREND)
    bull_test = [d for d in test_days if d in bull_days]
    nonbull_test = [d for d in test_days if d in nonbull_days]

    print(f"樣本：{len(df)} 列  {df['symbol'].nunique()} 檔  {len(days)} 交易日  "
          f"train={len(train_days)}  embargo={embargo}  test={len(test_days)}"
          f"（test 內多頭 {len(bull_test)} / 非多頭 {len(nonbull_test)} 日）\n")

    for h in horizons:
        res = _fit_and_score(df, train_days, test_days, h, min_names)
        _print_horizon_report(h, res, test_days, bull_test, nonbull_test, min_names)

    print("=== 結論提醒 ===")
    print("上面所有數字都是同一批資料上的 retrospective 研究結果，不是 honest OOS；"
          "『test』只代表 embargo 之後只 predict 不再 fit 的段落，不代表通過 §B.4 的啟用門檻"
          "（|NW t|>2、train/test 同向、跨 regime 方向一致、有效 test 日數達標）。"
          "即使某個 horizon 看似通過，也只能當作『值得繼續投入 §B.2』的訊號，"
          "不能直接當結論或拿去改權重。")


if __name__ == "__main__":
    asyncio.run(_amain())
