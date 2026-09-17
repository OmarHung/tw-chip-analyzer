"""系統狀態:資料涵蓋度查詢(供 /api/ops/status)。

純唯讀彙總:各資料源的交易日數/日期範圍/列數,以及逐筆(raw_tick)近期每日
灌了幾檔——用來一眼看出「哪天逐筆被 Shioaji 配額砍到只剩幾檔」這類問題。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chips import (
    InstitutionalDaily,
    MarginDaily,
    SblDaily,
    TdccSummaryWeekly,
)
from app.db.models.features import FeatureDaily
from app.db.models.intraday import RawTick
from app.db.models.market import (
    CorporateAction,
    DailyPrice,
    FuturesDaily,
    MarketDaily,
)


async def _date_span(session: AsyncSession, col) -> dict:
    """某資料表的 data_date 涵蓋:交易日數(distinct)、最早、最新。"""
    stmt = select(
        func.count(func.distinct(col)),
        func.min(col),
        func.max(col),
    )
    days, dmin, dmax = (await session.execute(stmt)).one()
    return {
        "days": int(days or 0),
        "min": str(dmin) if dmin else None,
        "max": str(dmax) if dmax else None,
    }


async def _dates(session: AsyncSession, col) -> list[dt.date]:
    """某資料表出現過的 data_date 清單(升冪),供缺口比對。"""
    rows = (await session.execute(select(col).distinct().order_by(col))).scalars().all()
    return [d for d in rows if d is not None]


def _span_of(dates: list[dt.date]) -> dict:
    return {
        "days": len(dates),
        "min": str(dates[0]) if dates else None,
        "max": str(dates[-1]) if dates else None,
    }


def _with_gaps(dates: list[dt.date], calendar: list[dt.date]) -> dict:
    """相對交易日曆(以 daily_price 為準)算缺口:缺哪幾日 + 起始日之前的落差。

    **只算該源起始日「之後」的空洞**。起始日之前沒有資料不是缺漏,而是這個源
    本來就從那天才開始——feature_daily 需要 20 日回看視窗、market_daily 需要
    MA60,前面那段永遠不會有值。把它們算成缺口會誤導成「該補」,一鍵補齊也會
    去補一堆補不出東西的日子(實際踩過:特徵顯示「缺 20」但那 20 天正是視窗)。
    落差另以 starts_late 表達,語意是「比日曆晚開始幾個交易日」。

    日曆本身也可能不完整(整條鏈都沒補的那天不會出現在任何表),故這裡答的是
    「相對已知交易日還缺幾日」,不是「相對台股官方行事曆」。
    """
    have = set(dates)
    start = dates[0] if dates else None
    within = [d for d in calendar if start is not None and d >= start]
    missing = [d for d in within if d not in have]
    return {
        **_span_of(dates),
        "missing": len(missing),
        # 完整清單:供 UI 展開檢視與「補齊缺漏」一鍵回補(通常只有個位數)
        "missing_dates": [str(d) for d in missing],
        "starts_late": sum(1 for d in calendar if start is not None and d < start),
    }


async def _row_count(session: AsyncSession, model) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(model))).scalar() or 0
    )


# ── raw_tick 專用:避免全表聚合 ─────────────────────────────────────────────
#
# raw_tick 是這裡唯一的巨表(線上 2026-09 已達 4,135 萬列,且每日 +30 萬)。對它做
# count(*) / count(distinct data_date) / 無下界的 group by,在 1 vCPU、PG page cache
# 冷掉之後要跑數十秒到數分鐘,期間吃滿 CPU 與磁碟——同機的 API 與 Next.js SSR 會一起
# 被拖慢。實際事故(2026-09-15):行程重啟後首發涵蓋刷新讓首頁 SSR 超過 nginx 的 60s
# 逾時,使用者看到 504。以下三招把成本壓到與資料量幾乎無關:
#
#   1. 日期清單用 loose index scan(下面的遞迴 CTE)。PG 沒有 index skip scan,
#      count(distinct data_date) 必須掃完整個索引;遞迴取 min 則是「每個相異日期
#      一次索引探測」,42 天就只有 42 次。實測 4,062 萬列:6,627ms → 4.5ms。
#   2. 逐日統計拆成兩個查詢。原本 `count(distinct symbol), count(*) GROUP BY data_date`
#      單一查詢即使走了 index-only scan(掃描本身只要 1.8s),仍會為了 distinct 去排序
#      2,659 萬列 → 落到磁碟排序、9.8s。拆開後:筆數用純 GroupAggregate(不必排序),
#      檔數改用「每日各跑一次 symbol 的 loose index scan」(每天約 2,000 次探測)。
#      實測 10,948ms → 936ms(721 + 215)。
#   3. 總列數改用 pg_class.reltuples 估計值(由 autovacuum/ANALYZE 維護),不做精確
#      count;回傳時標記為估計值,由 UI 顯示「約」。實測 674ms → 0.02ms。
#
# 三者都依賴 (data_date, symbol) 索引 —— 見 models/intraday.RawTick。
# 合計:單次涵蓋刷新 18.2s → 0.94s(本機 NVMe、4,062 萬列)。

_TICK_DATES_SQL = text(
    """
    WITH RECURSIVE d(data_date) AS (
        SELECT min(data_date) FROM raw_tick
        UNION ALL
        SELECT (SELECT min(t.data_date) FROM raw_tick t WHERE t.data_date > d.data_date)
        FROM d
        WHERE d.data_date IS NOT NULL
    )
    SELECT data_date FROM d WHERE data_date IS NOT NULL ORDER BY data_date
    """
)

_RELTUPLES_SQL = text("SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(:t)")

# 每個指定日期有幾檔(distinct symbol)。同樣是 loose index scan,只是這次在
# (data_date, symbol) 索引的第二層跳:固定 data_date、逐次取下一個更大的 symbol。
# 日期由呼叫端傳入(已經有清單了),避免在這裡再掃一次 distinct data_date。
_TICK_DAILY_SYMBOLS_SQL = text(
    """
    WITH RECURSIVE pairs(d, s) AS (
        SELECT d, (SELECT min(t.symbol) FROM raw_tick t WHERE t.data_date = d)
        FROM unnest(CAST(:dates AS date[])) AS d
        UNION ALL
        SELECT p.d, (
            SELECT min(t.symbol) FROM raw_tick t
            WHERE t.data_date = p.d AND t.symbol > p.s
        )
        FROM pairs p
        WHERE p.s IS NOT NULL
    )
    SELECT d, count(*) FROM pairs WHERE s IS NOT NULL GROUP BY d
    """
)


async def _tick_dates(session: AsyncSession) -> list[dt.date]:
    """raw_tick 出現過的 data_date(升冪),以 loose index scan 取得。"""
    rows = (await session.execute(_TICK_DATES_SQL)).scalars().all()
    return [d for d in rows if d is not None]


async def _tick_daily_stats(session: AsyncSession, dates: list[dt.date]) -> list[dict]:
    """指定日期的逐筆每日檔數與筆數(新到舊)。空清單直接回,不打 DB。"""
    if not dates:
        return []

    ticks = dict(
        (
            await session.execute(
                select(RawTick.data_date, func.count())
                .where(RawTick.data_date >= dates[0])
                .group_by(RawTick.data_date)
            )
        ).all()
    )
    symbols = dict(
        (await session.execute(_TICK_DAILY_SYMBOLS_SQL, {"dates": dates})).all()
    )
    return [
        {
            "date": str(d),
            "symbols": int(symbols.get(d, 0)),
            "ticks": int(ticks.get(d, 0)),
        }
        for d in sorted(dates, reverse=True)
    ]


async def _tick_row_count(session: AsyncSession) -> tuple[int, bool]:
    """raw_tick 列數與「是否為估計值」。

    reltuples 在從未 ANALYZE 過的表是 -1(PG14+)或 0;那種情況下表本身也還小,
    退回精確 count 不貴。
    """
    est = (await session.execute(_RELTUPLES_SQL, {"t": "raw_tick"})).scalar()
    if est is not None and int(est) > 0:
        return int(est), True
    return await _row_count(session, RawTick), False


async def load_coverage(session: AsyncSession, tick_days: int = 30) -> dict:
    """彙總各資料源涵蓋度 + 近 tick_days 天逐筆的每日檔數/筆數。"""
    # 日頻資料源:撈出實際有資料的日期,才能與交易日曆比對缺口。
    price_d = await _dates(session, DailyPrice.data_date)
    feature_d = await _dates(session, FeatureDaily.data_date)
    inst_d = await _dates(session, InstitutionalDaily.data_date)
    margin_d = await _dates(session, MarginDaily.data_date)
    sbl_d = await _dates(session, SblDaily.data_date)
    market_d = await _dates(session, MarketDaily.data_date)

    # 交易日曆基準:日線有資料的日 = 已知交易日。日線為空則退回大盤。
    calendar = price_d or market_d

    price = _with_gaps(price_d, calendar)
    feature = _with_gaps(feature_d, calendar)
    inst = _with_gaps(inst_d, calendar)
    margin = _with_gaps(margin_d, calendar)
    sbl = _with_gaps(sbl_d, calendar)
    market = _with_gaps(market_d, calendar)

    # 非日頻/非全覆蓋:週度(TDCC)、事件表(公司行動)、受配額限制(逐筆),
    # 對它們算「每個交易日都該有」沒有意義 → 不給 missing。
    tdcc = await _date_span(session, TdccSummaryWeekly.data_date)
    ca = await _date_span(session, CorporateAction.data_date)
    # 台指期同樣不給 missing:它不進分數,缺漏用專屬的 backfill_futures(整月一次請求)補,
    # 不該被算進「一鍵補齊缺漏」的日子聯集而觸發整條 EOD 逐日重跑。
    futures = await _date_span(session, FuturesDaily.data_date)

    # raw_tick 走專用路徑(見上方註解):日期清單以 loose index scan 取得,再拿最近
    # tick_days 天去算逐日統計——原本的 LIMIT 是在聚合「之後」才套用,限縮不到掃描。
    tick_dates = await _tick_dates(session)
    tick = _span_of(tick_dates)
    tick_rows = await _tick_daily_stats(session, tick_dates[-tick_days:])
    tick_count, tick_estimated = await _tick_row_count(session)

    return {
        "sources": {
            "feature_daily": feature,
            "daily_price": price,
            "institutional_daily": inst,
            "margin_daily": margin,
            "tdcc_summary_weekly": tdcc,
            "sbl_daily": sbl,
            "market_daily": market,
            "corporate_action": ca,
            "futures_daily": futures,
            "raw_tick": tick,
        },
        # 缺口比對的基準日曆(= daily_price 有資料的交易日)。
        "calendar": _span_of(calendar),
        "row_counts": {
            "raw_tick": tick_count,
            "feature_daily": await _row_count(session, FeatureDaily),
            "daily_price": await _row_count(session, DailyPrice),
        },
        # 哪些 row_counts 是估計值(UI 顯示「約」)。
        "row_counts_estimated": ["raw_tick"] if tick_estimated else [],
        "tick_by_date": tick_rows,
    }
