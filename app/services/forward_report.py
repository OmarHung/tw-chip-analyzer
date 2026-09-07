"""前瞻驗證報告:signal_snapshot 分數 vs 之後「實現」報酬(§28 的活體驗證)。

與 backtest 引擎同一定義(向量化以便 request-time 服務):
- 進場 = data_date 之後第一根 bar 的 open(look-ahead 安全)
- 出場 = 自進場 bar 起第 k 根的 close(k=horizon)
- net_return 用 CostModel(禁 0 成本)
每天 EOD 落地新 snapshot 後,已實現的 forward 樣本自然增加——牆上時間
每走一天,這份報告就多一天「先寫死預測、後看結果」的誠實 OOS 證據。

快取:以最新 snapshot 日為 key(當日內重用)。
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.costs import CostModel
from app.core.config import get_thresholds

_cache: dict[dt.date, dict] = {}


def _bucket_label(score: float, buckets: list[list[int]]) -> str | None:
    for lo, hi in buckets:
        if lo <= score < hi:
            return f"[{lo},{hi})"
    return None


async def build_forward_report(session: AsyncSession) -> dict:
    latest = (
        await session.execute(text("select max(data_date) from signal_snapshot"))
    ).scalar()
    if latest is None:
        return {"as_of": None, "horizons": [], "total_signals": 0}
    if latest in _cache:
        return _cache[latest]

    bt = get_thresholds().backtest
    horizons: list[int] = list(bt.get("horizons", [1, 3, 5, 10, 20]))
    buckets: list[list[int]] = bt.get("score_buckets", [])
    costs = CostModel.from_config()

    snaps = pd.DataFrame(
        (await session.execute(text(
            "select symbol, data_date, chip_score from signal_snapshot"
        ))).all(),
        columns=["symbol", "data_date", "score"],
    )
    prices = pd.DataFrame(
        (await session.execute(text(
            "select symbol, data_date, open, close from daily_price "
            "where open is not null and close is not null "
            "order by symbol, data_date"
        ))).all(),
        columns=["symbol", "data_date", "open", "close"],
    )
    if snaps.empty or prices.empty:
        return {"as_of": str(latest), "horizons": [], "total_signals": 0}

    prices["open"] = prices["open"].astype(float)
    prices["close"] = prices["close"].astype(float)

    # 每檔:進場價(次一交易日 open)與各 horizon 出場價(自進場起第 k 根 close)
    g = prices.groupby("symbol", group_keys=False)
    prices["entry"] = g["open"].shift(-1)
    for k in horizons:
        prices[f"exit{k}"] = g["close"].shift(-k)

    df = snaps.merge(
        prices[["symbol", "data_date", "entry", *(f"exit{k}" for k in horizons)]],
        on=["symbol", "data_date"], how="left",
    )
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
                          "ic_t": None, "ic_days": 0})
            continue
        # bucket 彙總
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
        # 逐日橫斷面 rank IC → 平均 + t
        ics = []
        for _, day in sub.groupby("data_date"):
            if len(day) < 20 or day["score"].nunique() <= 1:
                continue
            ics.append(float(np.corrcoef(day["score"].rank(), day["net"].rank())[0, 1]))
        ic = ic_t = None
        if len(ics) >= 2:
            a = np.array(ics)
            std = a.std(ddof=1)
            ic = round(float(a.mean()), 4)
            ic_t = round(float(a.mean() / std * math.sqrt(a.size)), 2) if std > 0 else 0.0
        out_h.append({
            "horizon": k, "n": int(len(sub)), "buckets": brows,
            "ic": ic, "ic_t": ic_t, "ic_days": len(ics),
        })

    report = {
        "as_of": str(latest),
        "total_signals": int(len(snaps)),
        "evaluated_latest_pending": int((df[f"exit{min(horizons)}"].isna()).sum()),
        "horizons": out_h,
    }
    _cache.clear()
    _cache[latest] = report
    return report
