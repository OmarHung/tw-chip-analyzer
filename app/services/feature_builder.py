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
from app.db.models.market import DailyPrice, Stock
from app.core.config import get_thresholds
from app.importers.base import availability_for
from app.repositories.corporate_actions import load_factors
from app.repositories.upsert import upsert_many
from app.services.orderflow_intraday import INTRADAY_SIGNAL_KEYS, compute_orderflow
from app.services.normalize import squash_z
from app.services.price_adjust import back_adjust

LOTS_TO_SHARES = 1000  # 融資融券單位為張
SBL_LOOKBACK = 20  # 借券餘額百分比變化的回看交易日數（OOS 上 20D > 5D）
FLOW_WINDOW = 5  # 法人淨額 / 融資券餘額變化的交易日窗
# 固定視窗特徵的最低觀測數（docs/09 BUG-10）：不足即 NULL，不以較短資料冒充 N 日值
MA_BARS = 20
ATR_BARS = 15  # 14 個 TR 需要 15 根（含前一根收盤）
_MIN_INDUSTRY_MEMBERS = 5  # 產業成分股門檻：不足者不給趨勢分（樣本太少不成趨勢）


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

    # 各標的有界訊號（皆 turnover-neutral 比率/正規化值）；鍵對應與 /orderflow 單檔分數共用
    signals: dict[str, dict[str, float]] = {k: {} for k in INTRADAY_SIGNAL_KEYS}
    for sym, ticks in by_symbol.items():
        of = compute_orderflow(ticks)
        if of.trade_count == 0:
            continue
        for key, attr in INTRADAY_SIGNAL_KEYS.items():
            signals[key][sym] = getattr(of, attr)

    z = {key: _zscore_map(vals) for key, vals in signals.items() if key != "obi"}
    return {
        sym: {
            "cvd_z": z["cvd"].get(sym, 0.0),
            "large_trade_delta_z": z["large_trade_delta"].get(sym, 0.0),
            # obi 為 CVD 斜率的每分鐘量正規化值（已有界），不做 z
            "intraday_obi": signals["obi"].get(sym, 0.0),
            "absorption_z": z["absorption"].get(sym, 0.0),
            "trade_speed_z": z["trade_speed"].get(sym, 0.0),
            "price_efficiency_z": z["price_efficiency"].get(sym, 0.0),
        }
        for sym in signals["cvd"]
    }


def _zscore_map(strength: dict[str, float]) -> dict[str, float]:
    """對一組 {symbol: value} 做橫斷面 Z-score。

    樣本不足（<2）時 z 無定義 → 回空（呼叫端存 NULL），不可給中性 0 冒充有資料；
    全部同值才是真的中性（z=0）。
    """
    if len(strength) < 2:
        return {}
    cfg = get_thresholds().get("features", default={}) or {}
    q = float(cfg.get("winsorize_quantile", 0.0))
    z_clip = float(cfg.get("z_clip", np.inf))
    vals = np.array(list(strength.values()), dtype=float)
    # 單一離群值會撐大 std、把其餘標的 z 壓扁：原始值先截尾（保序），z 再夾上限
    if q > 0:
        lo, hi = np.quantile(vals, [q, 1 - q])
        vals = np.clip(vals, lo, hi)
    mean, std = float(vals.mean()), float(vals.std(ddof=0))
    if std == 0:
        return {s: 0.0 for s in strength}
    z = np.clip((vals - mean) / std, -z_clip, z_clip)
    return {s: float(v) for s, v in zip(strength, z)}


async def _load_df(
    session: AsyncSession, model, cols, start, end, as_of: dt.datetime | None = None
) -> pd.DataFrame:
    """視窗內的資料；as_of 另以 `available_at` 過濾（鐵則 8 的 look-ahead 防護）。

    日線/法人/融資/借券的 available_at 就是當日盤後，與 `data_date <= target` 等價；
    但 **TDCC 是週五統計、數日後才揭露**，只看 data_date 會在還不知道的時候就用它算
    特徵。故一律傳 as_of=目標日盤後，讓「何時可用」由資料自己的 available_at 決定。
    """
    stmt = select(*[getattr(model, c) for c in cols]).where(
        model.data_date >= start, model.data_date <= end
    )
    if as_of is not None:
        stmt = stmt.where(model.available_at <= as_of)
    rows = (await session.execute(stmt)).all()
    return pd.DataFrame(rows, columns=cols)


def _price_features(
    g: pd.DataFrame,
    actions: list[tuple[dt.date, float]] | None = None,
    share_actions: list[tuple[dt.date, float]] | None = None,
    thresholds=None,
) -> dict | None:
    """g：單一 symbol、依日期排序、data_date<=target 的價格。

    actions：該檔公司行動的價格因子 [(ex_date, adj_factor)]。有事件時對 close/high/low
    做後復權（最新一根不動、較早乘上其後因子累積），使 MA/ATR/日報酬跨除權息連續。
    vwap 為當日 turnover/volume（同日比值），不受跨日還原影響。

    share_actions：股數因子 [(ex_date, share_factor)]（拆股/配股 >1、減資 <1）。拆股後
    1 張舊股變 N 張，歷史量與現量尺度不同，會把 avg_vol20 壓小 N 倍——而 avg_vol20 是
    法人/融資/借券強度的分母。故歷史量同樣後復權成「現在的股數單位」。
    """
    g = g.sort_values("data_date")
    if g.empty:
        return None
    if actions:
        dates = list(g["data_date"])
        adj_close = back_adjust(dates, g["close"].astype(float), actions)
        adj_high = back_adjust(dates, g["high"].astype(float), actions)
        adj_low = back_adjust(dates, g["low"].astype(float), actions)
        close = pd.Series(adj_close, index=g.index).astype(float)
        high = pd.Series(adj_high, index=g.index).astype(float)
        low = pd.Series(adj_low, index=g.index).astype(float)
    else:
        close = g["close"].astype(float)
        high = g["high"].astype(float)
        low = g["low"].astype(float)
    vol = g["volume"].astype(float)
    turn = g["turnover"].astype(float)
    if share_actions:
        adj_vol = pd.Series(
            back_adjust(list(g["data_date"]), vol, share_actions), index=g.index
        ).astype(float)
    else:
        adj_vol = vol
    last_close = close.iloc[-1]
    if not np.isfinite(last_close) or last_close <= 0:
        return None

    n = len(close)
    ma20 = close.tail(MA_BARS).mean() if n >= MA_BARS else np.nan
    # ATR14
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr14 = tr.tail(ATR_BARS - 1).mean() if n >= ATR_BARS else np.nan
    last_vol = vol.iloc[-1]  # vwap 是同日 turnover/volume 比值，須用原始量
    last_turn = turn.iloc[-1]
    vwap = (last_turn / last_vol) if last_vol and last_vol > 0 else last_close
    swing_low = low.tail(10).min()
    t = thresholds or get_thresholds()
    # 壓力位（RR 目標，docs/09 BUG-01）：近 N 根後復權最高價；bar 數不足 → NULL
    rk = t.risk
    resistance = (
        float(high.tail(rk["resistance_lookback_bars"]).max())
        if len(high) >= rk["resistance_min_bars"]
        else None
    )
    avg_vol20 = adj_vol.tail(MA_BARS).mean() if n >= MA_BARS else np.nan
    # 近 5 日報酬（後復權價，故跨除權息連續）→ 產業趨勢用；不足 6 根 → NULL
    base5 = close.iloc[-(FLOW_WINDOW + 1)] if n >= FLOW_WINDOW + 1 else np.nan
    ret5 = (
        float((last_close - base5) / base5)
        if np.isfinite(base5) and base5 > 0
        else None
    )
    prev_close = close.iloc[-2] if len(close) >= 2 else None
    change_pct = (
        float((last_close - prev_close) / prev_close)
        if prev_close and prev_close > 0
        else None
    )
    # 鎖死漲停（docs/09 BUG-03）：漲幅達門檻且一價到底（原始 high=low=close）。
    # 前收以後復權價比較（除權息日即近似參考價）；無前一根 → 無法判斷(NULL)。
    raw = g.iloc[-1]
    is_limit_locked = (
        bool(
            change_pct >= t.entry_filter["limit_lock_min_change_pct"]
            and raw["high"] == raw["low"] == raw["close"]
        )
        if change_pct is not None
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
        "close_vs_ma20_pct": (
            float((last_close - ma20) / ma20) if np.isfinite(ma20) and ma20 else None
        ),
        "close_vs_vwap_pct": float((last_close - vwap) / vwap) if vwap else None,
        "resistance_high": round(resistance, 4) if resistance is not None and np.isfinite(resistance) else None,
        "is_limit_locked": is_limit_locked,
        "_avg_vol20": float(avg_vol20) if np.isfinite(avg_vol20) and avg_vol20 > 0 else None,
        "_ret5": ret5,
    }


def _net_window(
    g: pd.DataFrame, col: str, window_dates: list[dt.date]
) -> float | None:
    """市場最近 N 個交易日（window_dates）內的淨額加總。

    只看市場交易日窗，不取「該檔自己的最後 N 筆」——來源對無交易的標的會漏列，
    後者會一路往回撈到數週前（docs/09 BUG-10）。窗內漏列的日子視為 0（來源不列
    無成交者）；窗內完全沒有列 → 無資料（NULL），不冒充中性。
    """
    in_win = g[g["data_date"].isin(set(window_dates))]
    if in_win.empty:
        return None
    return float(in_win[col].fillna(0).sum())


def _asof_change(
    g: pd.DataFrame,
    col: str,
    market_dates: list[dt.date],
    lookback: int,
    pct: bool = False,
) -> float | None:
    """餘額相對 lookback 個市場交易日前的變化（pct=True 為百分比）。

    - 最新值必須落在目標日（market_dates[-1]），否則是舊資料。
    - 基期取「lookback 個交易日前當日或更早」的最後一筆；沒有 → 歷史不足 → NULL。
      不再用最早一筆湊出名義上的 N 日變化（docs/09 BUG-10）。

    借券用 pct=True（OOS 2026-09-09：百分比版本 train/test 同號且 test 顯著，
    絕對變化 ÷ 均量版本 t≈0，因借券部位變動與成交量無關）。
    """
    if len(market_dates) < lookback + 1:
        return None
    s_ = g.sort_values("data_date").dropna(subset=[col])
    if s_.empty or s_["data_date"].iloc[-1] != market_dates[-1]:
        return None
    base_rows = s_[s_["data_date"] <= market_dates[-(lookback + 1)]]
    if base_rows.empty:
        return None
    base = float(base_rows[col].iloc[-1])
    latest = float(s_[col].iloc[-1])
    if not pct:
        return latest - base
    if base <= 0:
        return None
    return (latest - base) / base


async def _industry_trend(
    session: AsyncSession, pf: dict[str, dict]
) -> dict[str, float]:
    """各股所屬產業的趨勢分數（-1..1）。

    產業分數 = 成分股近 5 日報酬的**中位數**（中位數避開單一大漲股拉抬整個產業），
    再對「產業」做橫斷面 z + squash → -1..1，最後展開回個股。成分股不足
    `_MIN_INDUSTRY_MEMBERS` 的產業不給分（樣本太少不成趨勢），該股維持 NULL 中性。

    產業別取自 `stock.industry`（中文名稱，上市/上櫃同名同組）。importer 尚未帶
    產業別的個股（或 ETF/下市 stub）自然落在 NULL，不參與。
    """
    rets = {s: f["_ret5"] for s, f in pf.items() if f.get("_ret5") is not None}
    if not rets:
        return {}
    rows = (
        await session.execute(
            select(Stock.symbol, Stock.industry).where(
                Stock.symbol.in_(list(rets)), Stock.industry.is_not(None)
            )
        )
    ).all()
    members: dict[str, list[str]] = {}
    for sym, ind in rows:
        members.setdefault(ind, []).append(sym)

    ind_score = {
        ind: float(np.median([rets[s] for s in syms]))
        for ind, syms in members.items()
        if len(syms) >= _MIN_INDUSTRY_MEMBERS
    }
    z = _zscore_map(ind_score)
    return {
        sym: squash_z(z[ind])
        for ind, syms in members.items()
        if ind in z
        for sym in syms
    }


async def build_features(session: AsyncSession, target: dt.date) -> int:
    t = get_thresholds()
    # 價格視窗須涵蓋壓力位回看根數；法人/信用等 5/20 日特徵仍只用 tail，視窗較寬無副作用
    start = target - dt.timedelta(days=t.get("features", "price_window_days", default=45))

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
    # TDCC 視窗放寬到 60 天:揭露落後會讓「target 當下已可用」的快照往前挪,
    # 30 天視窗在 lag 之後可能一週都撈不到。以 available_at 決定可用性。
    tdcc = await _load_df(
        session, TdccSummaryWeekly,
        ["symbol", "data_date", "retail_ratio", "large_ratio", "super_large_ratio", "holder_count"],
        target - dt.timedelta(days=60), target, as_of=availability_for(target),
    )

    # 只處理當日有價格的個股
    symbols_today = set(prices.loc[prices["data_date"] == target, "symbol"])
    if not symbols_today:
        return 0

    # 公司行動（視窗內）→ 價格序列後復權修 MA/ATR/日報酬的斷點；股數變動的事件
    # （拆股/配股/減資）另把歷史量還原成現在的股數單位，避免污染 avg_vol20。
    actions, share_actions = await load_factors(
        session, symbols=list(symbols_today), start=start, end=target
    )

    pf: dict[str, dict] = {}
    for sym, g in prices.groupby("symbol"):
        if sym not in symbols_today:
            continue
        feat = _price_features(g, actions.get(sym), share_actions.get(sym), t)
        if feat:
            pf[sym] = feat

    # 市場交易日曆（視窗內有行情的日子）：5/20 日窗一律以此為準，不看個股自己的列數
    market_dates = sorted(d for d in prices["data_date"].unique() if d <= target)
    flow_window = market_dates[-FLOW_WINDOW:] if len(market_dates) >= FLOW_WINDOW else []

    # 個股層強度（除以 20 日均量做流動性正規化）
    foreign_str, trust_str, dealer_str = {}, {}, {}
    if not inst.empty and flow_window:
        for sym, g in inst.groupby("symbol"):
            av = pf.get(sym, {}).get("_avg_vol20")
            if not av:
                continue
            f_net = _net_window(g, "foreign_net", flow_window)
            if f_net is None:
                continue
            foreign_str[sym] = f_net / av
            trust_str[sym] = (_net_window(g, "trust_net", flow_window) or 0.0) / av
            dealer = (_net_window(g, "dealer_self_net", flow_window) or 0.0) + (
                _net_window(g, "dealer_hedge_net", flow_window) or 0.0
            )
            dealer_str[sym] = dealer / av

    margin_chg, short_chg = {}, {}
    if not margin.empty:
        for sym, g in margin.groupby("symbol"):
            av = pf.get(sym, {}).get("_avg_vol20")
            if not av:
                continue
            mc = _asof_change(g, "margin_balance", market_dates, FLOW_WINDOW)
            sc = _asof_change(g, "short_balance", market_dates, FLOW_WINDOW)
            if mc is not None:
                margin_chg[sym] = mc * LOTS_TO_SHARES / av
            if sc is not None:
                short_chg[sym] = sc * LOTS_TO_SHARES / av

    # 借券 SBL 餘額 20 日**百分比**變化(相對自身部位;見 _balance_pct_change 的
    # OOS 依據——舊的「絕對變化 ÷ 均量」版本實測無訊號)。
    sbl_chg: dict[str, float] = {}
    if not sbl.empty:
        for sym, g in sbl.groupby("symbol"):
            pc = _asof_change(g, "sbl_balance", market_dates, SBL_LOOKBACK, pct=True)
            if pc is not None:
                sbl_chg[sym] = pc

    # TDCC 大戶/散戶:視窗內有 >=2 個快照日 → 真實 week-over-week change;
    # 只有 1 週 → 橫斷面 level proxy(資料累積到位自動切換,見 docs/03 §10)。
    large_conc, retail_conc, holder_cnt_chg = {}, {}, {}
    if not tdcc.empty:
        tdcc_dates = sorted(tdcc["data_date"].unique())
        # 涵蓋率門檻:只有當「足夠多的個股都有 >=2 週」才切 change,否則全市場
        # 一致用 level proxy。部分回補期間(少數重點標的有歷史)不會讓其餘個股
        # 因算不出 change 而被排除 holder 成分。門檻見 config tdcc。
        multi = tdcc.groupby("symbol")["data_date"].nunique()
        coverage = (
            float((multi >= 2).sum()) / len(symbols_today) if symbols_today else 0.0
        )
        min_cov = get_thresholds().tdcc.get("change_coverage_min", 0.5)
        use_change = len(tdcc_dates) >= 2 and coverage >= min_cov
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

    industry_trend = await _industry_trend(session, pf)

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
                # 法人/信用/借券：缺資料或窗不足 → NULL（docs/09 BUG-11），由 analysis
                # 在 institutional 成分內重分配權重，全缺則排除整個成分
                "foreign_5d_z": z_foreign.get(sym),
                "trust_5d_z": z_trust.get(sym),
                "dealer_5d_z": z_dealer.get(sym),
                "margin_balance_change_z": z_margin.get(sym),
                "short_balance_change_z": z_short.get(sym),
                "sbl_change_z": z_sbl.get(sym),
                # 產業趨勢：無產業別/成分股不足 → NULL（market_score 視為中性）
                "industry_trend_score": industry_trend.get(sym),
                # TDCC:>=2 週快照時為真實 week-over-week change,否則 level proxy。
                # **無 TDCC 資料者為 NULL**(不是中性 0)——以中性值灌水會稀釋
                # composite(同 intraday 的混排問題),改由 composite 排除該成分。
                "large_holder_ratio_change_z": z_large_holder.get(sym),
                "retail_holder_ratio_change_z": z_retail_holder.get(sym),
                "holder_count_change_z": z_holder_cnt.get(sym),
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
