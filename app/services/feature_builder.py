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

from app.db.models.chips import (
    InstitutionalDaily,
    MarginDaily,
    SblDaily,
    TdccSummaryWeekly,
)
from app.db.models.features import FeatureDaily
from app.db.models.intraday import RawTick
from app.db.models.market import DailyPrice
from app.importers.base import availability_for
from app.repositories.upsert import upsert_many
from app.services.orderflow_intraday import compute_orderflow

LOTS_TO_SHARES = 1000  # 融資融券單位為張


def _tick_epoch(ts: dt.datetime) -> int:
    """RawTick.ts 為 naive 台北牆鐘；視為 UTC 秒（僅供每分鐘取樣分桶）。"""
    return int(ts.replace(tzinfo=dt.timezone.utc).timestamp())


async def _intraday_signals(
    session: AsyncSession, target: dt.date
) -> dict[str, dict[str, float]]:
    """對「當日有逐筆的標的」用 compute_orderflow 取有界訊號，做橫斷面 Z-score。

    只讀 data_date == target 的 raw_tick（look-ahead：盤中資料收盤後才可用，
    available_at 已在盤後）。回傳 {symbol: {cvd_z, large_trade_delta_z, intraday_obi}}。
    無逐筆時回空 dict → 該日所有標的 intraday 欄位維持 NULL。
    """
    rows = (
        await session.execute(
            select(
                RawTick.symbol, RawTick.ts, RawTick.price,
                RawTick.volume, RawTick.aggressor_side,
            ).where(RawTick.data_date == target).order_by(RawTick.symbol, RawTick.ts)
        )
    ).all()
    if not rows:
        return {}

    by_symbol: dict[str, list[dict]] = {}
    for sym, ts, price, volume, side in rows:
        by_symbol.setdefault(sym, []).append(
            {"t": _tick_epoch(ts), "price": float(price),
             "volume": int(volume), "side": int(side) if side is not None else 0}
        )

    # 各標的有界訊號（皆 turnover-neutral 比率/正規化值）
    net_aggr, large_net, obi = {}, {}, {}
    absorp, tspeed, peff = {}, {}, {}
    for sym, ticks in by_symbol.items():
        of = compute_orderflow(ticks)
        if of.trade_count == 0:
            continue
        net_aggr[sym] = of.net_aggressor
        large_net[sym] = of.large_net
        obi[sym] = of.cvd_slope_norm
        absorp[sym] = of.absorption_signal
        tspeed[sym] = of.trade_speed_signal
        peff[sym] = of.price_efficiency

    z_cvd = _zscore_map(net_aggr)          # net aggressor = 正規化 CVD 方向
    z_large = _zscore_map(large_net)       # 大單淨額方向
    z_absorp = _zscore_map(absorp)         # 吸收（低檔承接 vs 高檔賣壓）
    z_tspeed = _zscore_map(tspeed)         # 盤中成交加速
    z_peff = _zscore_map(peff)             # 價格路徑效率
    return {
        sym: {
            "cvd_z": z_cvd.get(sym, 0.0),
            "large_trade_delta_z": z_large.get(sym, 0.0),
            "intraday_obi": obi.get(sym, 0.0),
            "absorption_z": z_absorp.get(sym, 0.0),
            "trade_speed_z": z_tspeed.get(sym, 0.0),
            "price_efficiency_z": z_peff.get(sym, 0.0),
        }
        for sym in net_aggr
    }


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
    sbl = await _load_df(
        session, SblDaily,
        ["symbol", "data_date", "sbl_balance"],
        start, target,
    )
    # TDCC 週資料：取 data_date<=target 的近期快照(供 level proxy 或真實 change)
    tdcc = await _load_df(
        session, TdccSummaryWeekly,
        ["symbol", "data_date", "retail_ratio", "large_ratio", "super_large_ratio", "holder_count"],
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

    # 借券 SBL 餘額 5 日變化(單位已是股數,直接除以 20 日均量)
    sbl_chg: dict[str, float] = {}
    if not sbl.empty:
        for sym, g in sbl.groupby("symbol"):
            av = pf.get(sym, {}).get("_avg_vol20")
            if not av:
                continue
            bc = _balance_change_5d(g, "sbl_balance")
            if bc is not None:
                sbl_chg[sym] = bc / av

    # TDCC 大戶/散戶:視窗內有 >=2 個快照日 → 真實 week-over-week change;
    # 只有 1 週 → 橫斷面 level proxy(資料累積到位自動切換,見 docs/03 §10)。
    large_conc, retail_conc, holder_cnt_chg = {}, {}, {}
    if not tdcc.empty:
        tdcc_dates = sorted(tdcc["data_date"].unique())
        use_change = len(tdcc_dates) >= 2
        srt = tdcc.sort_values("data_date")
        if use_change:
            for sym, g in srt.groupby("symbol"):
                if len(g) < 2:
                    continue  # 該檔僅一週 → 維持中性
                cur, prev = g.iloc[-1], g.iloc[-2]
                cur_large = float(cur["large_ratio"] or 0) + float(cur["super_large_ratio"] or 0)
                prev_large = float(prev["large_ratio"] or 0) + float(prev["super_large_ratio"] or 0)
                large_conc[sym] = cur_large - prev_large
                retail_conc[sym] = float(cur["retail_ratio"] or 0) - float(prev["retail_ratio"] or 0)
                pc, cc = prev["holder_count"], cur["holder_count"]
                if pc and cc and float(pc) > 0:
                    holder_cnt_chg[sym] = (float(cc) - float(pc)) / float(pc)
        else:
            latest = srt.groupby("symbol").tail(1).set_index("symbol")
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
    z_sbl = _zscore_map(sbl_chg)
    z_large_holder = _zscore_map(large_conc)
    z_retail_holder = _zscore_map(retail_conc)
    z_holder_cnt = _zscore_map(holder_cnt_chg)

    # 盤中 order flow 橫斷面 z（僅當日有逐筆的標的；其餘維持 NULL）
    intraday = await _intraday_signals(session, target)

    av_at = availability_for(target)
    rows: list[dict] = []
    for sym, feat in pf.items():
        feat = {k: v for k, v in feat.items() if not k.startswith("_")}
        intra = intraday.get(sym)
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
                "sbl_change_z": z_sbl.get(sym, 0.0),
                # TDCC:>=2 週快照時為真實 week-over-week change,否則 level proxy
                "large_holder_ratio_change_z": z_large_holder.get(sym, 0.0),
                "retail_holder_ratio_change_z": z_retail_holder.get(sym, 0.0),
                "holder_count_change_z": z_holder_cnt.get(sym, 0.0),
                # intraday：有逐筆才填，否則 NULL（composite 動態排除）
                "cvd_z": intra["cvd_z"] if intra else None,
                "large_trade_delta_z": intra["large_trade_delta_z"] if intra else None,
                "intraday_obi": intra["intraday_obi"] if intra else None,
                "absorption_z": intra["absorption_z"] if intra else None,
                "trade_speed_z": intra["trade_speed_z"] if intra else None,
                "price_efficiency_z": intra["price_efficiency_z"] if intra else None,
            }
        )

    n = await upsert_many(session, FeatureDaily, rows, ["symbol", "data_date"])
    await session.commit()
    return n
