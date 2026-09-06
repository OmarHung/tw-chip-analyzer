"""Feature builder：由原始表計算 feature_daily（見 docs/03 §9、docs/06 §18）。

正規化採兩段：
1. 個股層：法人 5 日淨額 / 20 日均量 → 流動性中性的「強度」。
2. 市場層：對當日全市場的強度做橫斷面 Z-score（跨股票可比較）。

Look-ahead：只使用 data_date <= target 的資料。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chips import InstitutionalDaily, MarginDaily, TdccSummaryWeekly
from app.db.models.features import FeatureDaily
from app.db.models.market import DailyPrice
from app.importers.base import availability_for
from app.repositories.upsert import upsert_many

LOTS_TO_SHARES = 1000  # 融資融券單位為張


def _zscore_map(strength: dict[str, float]) -> dict[str, float]:
    """對一組 {symbol: value} 做橫斷面 Z-score。樣本不足回全 0。"""
    if len(strength) < 2:
        return {s: 0.0 for s in strength}
    vals = np.array(list(strength.values()), dtype=float)
    mean, std = float(vals.mean()), float(vals.std(ddof=0))
    if std == 0:
        return {s: 0.0 for s in strength}
    return {s: (v - mean) / std for s, v in strength.items()}


async def _load_df(session: AsyncSession, model, cols, start, end) -> pd.DataFrame:
    stmt = select(*[getattr(model, c) for c in cols]).where(
        model.data_date >= start, model.data_date <= end
    )
    rows = (await session.execute(stmt)).all()
    return pd.DataFrame(rows, columns=cols)


def _price_features(g: pd.DataFrame) -> dict | None:
    """g：單一 symbol、依日期排序、data_date<=target 的價格。"""
    g = g.sort_values("data_date")
    if g.empty:
        return None
    close = g["close"].astype(float)
    high = g["high"].astype(float)
    low = g["low"].astype(float)
    vol = g["volume"].astype(float)
    turn = g["turnover"].astype(float)
    last_close = close.iloc[-1]
    if not np.isfinite(last_close) or last_close <= 0:
        return None

    ma20 = close.tail(20).mean()
    # ATR14
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr14 = tr.tail(14).mean()
    last_vol = vol.iloc[-1]
    last_turn = turn.iloc[-1]
    vwap = (last_turn / last_vol) if last_vol and last_vol > 0 else last_close
    swing_low = low.tail(10).min()
    avg_vol20 = vol.tail(20).mean()
    prev_close = close.iloc[-2] if len(close) >= 2 else None
    change_pct = (
        float((last_close - prev_close) / prev_close)
        if prev_close and prev_close > 0
        else None
    )

    return {
        "close": round(last_close, 4),
        "change_pct": change_pct,
        "atr14": round(float(atr14), 4) if np.isfinite(atr14) else None,
        "ma20": round(float(ma20), 4) if np.isfinite(ma20) else None,
        "vwap": round(float(vwap), 4),
        "recent_swing_low": round(float(swing_low), 4) if np.isfinite(swing_low) else None,
        "turnover": round(float(last_turn), 2) if np.isfinite(last_turn) else None,
        "close_vs_ma20_pct": float((last_close - ma20) / ma20) if ma20 else None,
        "close_vs_vwap_pct": float((last_close - vwap) / vwap) if vwap else None,
        "_avg_vol20": float(avg_vol20) if np.isfinite(avg_vol20) and avg_vol20 > 0 else None,
    }


def _net_5d(g: pd.DataFrame, col: str) -> float:
    return float(g.sort_values("data_date")[col].tail(5).fillna(0).sum())


def _balance_change_5d(g: pd.DataFrame, col: str) -> float | None:
    s = g.sort_values("data_date")[col].dropna()
    if len(s) < 2:
        return None
    lookback = s.iloc[-6] if len(s) >= 6 else s.iloc[0]
    return float(s.iloc[-1] - lookback)


async def build_features(session: AsyncSession, target: dt.date) -> int:
    start = target - dt.timedelta(days=45)

    prices = await _load_df(
        session, DailyPrice,
        ["symbol", "data_date", "high", "low", "close", "volume", "turnover"],
        start, target,
    )
    if prices.empty:
        return 0
    inst = await _load_df(
        session, InstitutionalDaily,
        ["symbol", "data_date", "foreign_net", "trust_net", "dealer_self_net", "dealer_hedge_net"],
        start, target,
    )
    margin = await _load_df(
        session, MarginDaily,
        ["symbol", "data_date", "margin_balance", "short_balance"],
        start, target,
    )
    # TDCC 週資料：取 data_date<=target 的最新一筆/每檔
    tdcc = await _load_df(
        session, TdccSummaryWeekly,
        ["symbol", "data_date", "retail_ratio", "large_ratio", "super_large_ratio"],
        target - dt.timedelta(days=30), target,
    )

    # 只處理當日有價格的個股
    symbols_today = set(prices.loc[prices["data_date"] == target, "symbol"])
    if not symbols_today:
        return 0

    pf: dict[str, dict] = {}
    for sym, g in prices.groupby("symbol"):
        if sym not in symbols_today:
            continue
        feat = _price_features(g)
        if feat:
            pf[sym] = feat

    # 個股層強度（除以 20 日均量做流動性正規化）
    foreign_str, trust_str, dealer_str = {}, {}, {}
    if not inst.empty:
        for sym, g in inst.groupby("symbol"):
            av = pf.get(sym, {}).get("_avg_vol20")
            if not av:
                continue
            foreign_str[sym] = _net_5d(g, "foreign_net") / av
            trust_str[sym] = _net_5d(g, "trust_net") / av
            dealer = _net_5d(g, "dealer_self_net") + _net_5d(g, "dealer_hedge_net")
            dealer_str[sym] = dealer / av

    margin_chg, short_chg = {}, {}
    if not margin.empty:
        for sym, g in margin.groupby("symbol"):
            av = pf.get(sym, {}).get("_avg_vol20")
            if not av:
                continue
            mc = _balance_change_5d(g, "margin_balance")
            sc = _balance_change_5d(g, "short_balance")
            if mc is not None:
                margin_chg[sym] = mc * LOTS_TO_SHARES / av
            if sc is not None:
                short_chg[sym] = sc * LOTS_TO_SHARES / av

    # TDCC 大戶/散戶集中度（Phase 1：以橫斷面 level 為 proxy；
    # 待累積 >=2 週快照後改為真實 week-over-week change，見 docs/03 §10）。
    large_conc, retail_conc = {}, {}
    if not tdcc.empty:
        latest = (
            tdcc.sort_values("data_date").groupby("symbol").tail(1).set_index("symbol")
        )
        for sym in symbols_today:
            if sym in latest.index:
                row = latest.loc[sym]
                large_conc[sym] = float(row["large_ratio"] or 0) + float(
                    row["super_large_ratio"] or 0
                )
                retail_conc[sym] = float(row["retail_ratio"] or 0)

    # 市場層橫斷面 Z-score
    z_foreign = _zscore_map(foreign_str)
    z_trust = _zscore_map(trust_str)
    z_dealer = _zscore_map(dealer_str)
    z_margin = _zscore_map(margin_chg)
    z_short = _zscore_map(short_chg)
    z_large_holder = _zscore_map(large_conc)
    z_retail_holder = _zscore_map(retail_conc)

    av_at = availability_for(target)
    rows: list[dict] = []
    for sym, feat in pf.items():
        feat = {k: v for k, v in feat.items() if not k.startswith("_")}
        rows.append(
            {
                "symbol": sym,
                "data_date": target,
                "available_at": av_at,
                **feat,
                "foreign_5d_z": z_foreign.get(sym, 0.0),
                "trust_5d_z": z_trust.get(sym, 0.0),
                "dealer_5d_z": z_dealer.get(sym, 0.0),
                "margin_balance_change_z": z_margin.get(sym, 0.0),
                "short_balance_change_z": z_short.get(sym, 0.0),
                "sbl_change_z": 0.0,  # 待 SBL importer
                # TDCC：large/retail 為橫斷面集中度 proxy；count 待真實 change
                "large_holder_ratio_change_z": z_large_holder.get(sym, 0.0),
                "retail_holder_ratio_change_z": z_retail_holder.get(sym, 0.0),
                "holder_count_change_z": 0.0,
            }
        )

    n = await upsert_many(session, FeatureDaily, rows, ["symbol", "data_date"])
    await session.commit()
    return n
