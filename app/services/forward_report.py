"""前瞻驗證報告:signal_snapshot 分數 vs 之後「實現」報酬(§28 的活體驗證)。

與離線 backtest(app.backtest.runner / engine)**同一口徑**(docs/09 BUG-05/15):
- 價格:runner.load_bars 的後復權 OHLC——拆股/除權息日不會出現假的巨大報酬
- 進場 = 市場次一交易日的 open;該日無成交(停牌)→ 丟棄訊號
- 出場 = 自進場 bar 起第 k 根的 close(k=horizon);net_return 用 CostModel(禁 0 成本)
- 逐日 rank IC 的 t 值用 Newey–West(lag=horizon−1):連續交易日的 k 日報酬高度重疊,
  樸素 t 會把 n 天當 n 個獨立樣本而高估顯著性。樸素 t 仍輸出(ic_t_naive)供對照。

每天 EOD 落地新 snapshot 後,已實現的 forward 樣本自然增加——牆上時間
每走一天,這份報告就多一天「先寫死預測、後看結果」的誠實 OOS 證據。

快取 key 含三張表的 max(updated_at) 與筆數(偵測同筆覆寫與刪除)及報告設定 hash
(backtest + validation.min_ic_names,docs/13 Phase 3):
重建分數、修正既有價格、更新公司行動因子後,不重啟 API 也會重算(docs/12 Phase 5)。
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.costs import CostModel
from app.backtest.metrics import newey_west_t
from app.backtest.runner import load_bars, market_calendar
from app.core.config import get_thresholds

_cache: dict[tuple, dict] = {}


def _min_ic_names(t) -> int:
    """當日橫斷面至少幾檔才算 rank IC（validation.min_ic_names）。"""
    return int(t.get("validation", "min_ic_names", default=20))


def _bucket_label(score: float, buckets: list[list[int]]) -> str | None:
    for lo, hi in buckets:
        if lo <= score < hi:
            return f"[{lo},{hi})"
    return None


def _ic_stats(ics: list[float], horizon: int) -> dict:
    """逐日 IC → 平均、Newey–West t(主)與樸素 t(對照)。"""
    if len(ics) < 2:
        return {"ic": None, "ic_t": None, "ic_t_naive": None}
    a = np.array(ics, dtype=float)
    std = a.std(ddof=1)
    naive = round(float(a.mean() / std * math.sqrt(a.size)), 2) if std > 0 else 0.0
    nw = newey_west_t(a, lags=max(horizon - 1, 0))
    return {
        "ic": round(float(a.mean()), 4),
        "ic_t": round(nw, 2) if nw is not None else None,
        "ic_t_naive": naive,
    }


def _report_config(t) -> dict:
    """報告實際讀取的全部設定；計算與 cache key 共用同一份解析值（docs/13 Phase 3）。"""
    return {
        "backtest": t.backtest,
        "validation": {"min_ic_names": _min_ic_names(t)},
    }


async def _cache_key(session: AsyncSession, report_config: dict) -> tuple:
    row = (
        await session.execute(text(
            "select (select max(data_date) from signal_snapshot),"
            " (select max(updated_at) from signal_snapshot),"
            " (select count(*) from signal_snapshot),"
            " (select max(updated_at) from daily_price),"
            " (select count(*) from daily_price),"
            " (select max(updated_at) from corporate_action),"
            " (select count(*) from corporate_action)"
        ))
    ).one()
    cfg_hash = hashlib.sha1(
        json.dumps(report_config, sort_keys=True, default=str).encode()
    ).hexdigest()
    return (*row, cfg_hash)


def _price_frame(bars_by_sym: dict, horizons: list[int]) -> pd.DataFrame:
    """每檔 bar → 訊號日可用的進場價(市場次一交易日 open)與各 horizon 出場價。"""
    calendar = market_calendar(bars_by_sym)
    next_day = dict(zip(calendar[:-1], calendar[1:]))
    frames = []
    for sym, bars in bars_by_sym.items():
        if not bars:
            continue
        f = pd.DataFrame(
            {
                "data_date": [b.date for b in bars],
                "open": [b.open for b in bars],
                "close": [b.close for b in bars],
            }
        )
        f["symbol"] = sym
        nxt_date = f["data_date"].shift(-1)
        on_time = nxt_date == f["data_date"].map(next_day)
        f["entry"] = f["open"].shift(-1).where(on_time)
        for k in horizons:
            f[f"exit{k}"] = f["close"].shift(-k)
        frames.append(f)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


async def build_forward_report(session: AsyncSession) -> dict:
    thresholds = get_thresholds()
    report_config = _report_config(thresholds)
    bt = report_config["backtest"]
    min_names = report_config["validation"]["min_ic_names"]
    key = await _cache_key(session, report_config)
    latest = key[0]
    if latest is None:
        return {"as_of": None, "horizons": [], "total_signals": 0}
    if key in _cache:
        return _cache[key]

    horizons: list[int] = list(bt.get("horizons", [1, 3, 5, 10, 20]))
    buckets: list[list[int]] = bt.get("score_buckets", [])
    costs = CostModel.from_config()
    entry_field = bt.get("entry_price", "open")

    snaps = pd.DataFrame(
        (await session.execute(text(
            "select symbol, data_date, chip_score from signal_snapshot"
        ))).all(),
        columns=["symbol", "data_date", "score"],
    )
    if snaps.empty:
        return {"as_of": str(latest), "horizons": [], "total_signals": 0}
    bars = await load_bars(session, sorted(snaps["symbol"].unique()))
    prices = _price_frame(bars, horizons)
    if prices.empty:
        return {"as_of": str(latest), "horizons": [], "total_signals": 0}
    if entry_field == "close":
        # 與 engine 一致:entry bar 的 close
        nxt_close = prices.groupby("symbol")["close"].shift(-1)
        prices["entry"] = nxt_close.where(prices["entry"].notna())

    df = snaps.merge(
        prices[["symbol", "data_date", "entry", *(f"exit{k}" for k in horizons)]],
        on=["symbol", "data_date"], how="left",
    )
    # pending 必須在丟棄無進場列之前計算（docs/12 Phase 5）：
    # - pending_entry：訊號日即最新交易日，下一交易日尚未發生（真正等待中，非停牌丟棄）
    # - pending_exit_min_horizon：已有進場但最短 horizon 的出場價尚未出現
    last_trading_day = prices["data_date"].max()
    min_h = min(horizons)
    pending_entry = int((df["data_date"] >= last_trading_day).sum())
    pending_exit = int((df["entry"].notna() & df[f"exit{min_h}"].isna()).sum())
    df = df[df["entry"] > 0]
    df["score"] = df["score"].astype(float)
    df["bucket"] = [_bucket_label(s, buckets) for s in df["score"]]

    paid = df["entry"] * (1 + costs.buy_cost_rate)
    out_h = []
    for k in horizons:
        received = df[f"exit{k}"] * (1 - costs.sell_cost_rate)
        net = (received - paid) / paid
        sub = pd.DataFrame({
            "data_date": df["data_date"], "score": df["score"],
            "bucket": df["bucket"], "net": net,
        }).dropna(subset=["net"])
        if sub.empty:
            out_h.append({"horizon": k, "n": 0, "buckets": [], "ic": None,
                          "ic_t": None, "ic_t_naive": None, "ic_days": 0})
            continue
        brows = []
        for lo, hi in buckets:
            label = f"[{lo},{hi})"
            b = sub[sub["bucket"] == label]["net"]
            brows.append({
                "label": label,
                "n": int(len(b)),
                "win_rate": round(float((b > 0).mean()), 4) if len(b) else None,
                "avg_net": round(float(b.mean()), 5) if len(b) else None,
            })
        ics = []
        for _, day in sub.groupby("data_date"):
            if len(day) < min_names or day["score"].nunique() <= 1:
                continue
            ics.append(float(np.corrcoef(day["score"].rank(), day["net"].rank())[0, 1]))
        out_h.append({
            "horizon": k, "n": int(len(sub)), "buckets": brows,
            **_ic_stats(ics, k), "ic_days": len(ics),
        })

    report = {
        "as_of": str(latest),
        "total_signals": int(len(snaps)),
        "pending_entry": pending_entry,
        "pending_exit_min_horizon": pending_exit,
        "evaluated_latest_pending": pending_entry + pending_exit,  # 相容舊前端欄位
        "horizons": out_h,
    }
    _cache.clear()
    _cache[key] = report
    return report
