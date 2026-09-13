"""逐日重建 composite 分數並落地 signal_snapshot(歷史批次;供 backtest 驗證)。

單日落地邏輯已抽到 app.services.signal_persist.persist_signals,由 daily job
(每日一天)與本腳本(歷史批次)共用,單一真相來源。本腳本負責:對每個交易日
build_features(look-ahead 安全,只讀 data_date<=t)+ persist_signals。

用法:
  APP_ENV=dev python -m scripts.rebuild_signals                 # 全部已回填交易日
  APP_ENV=dev python -m scripts.rebuild_signals --min-lookback 20
  APP_ENV=dev python -m scripts.rebuild_signals --start 2026-04-01 --end 2026-09-05
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models.market import DailyPrice
from app.db.session import get_sessionmaker
from app.services.feature_builder import build_features
from app.services.market_score import build_market_daily
from app.services.signal_persist import persist_signals

logger = get_logger("scripts.rebuild_signals")


async def _trading_days(start: dt.date | None, end: dt.date | None) -> list[dt.date]:
    sm = get_sessionmaker()
    async with sm() as s:
        stmt = select(DailyPrice.data_date).distinct().order_by(DailyPrice.data_date)
        if start is not None:
            stmt = stmt.where(DailyPrice.data_date >= start)
        if end is not None:
            stmt = stmt.where(DailyPrice.data_date <= end)
        return [d for (d,) in (await s.execute(stmt)).all()]


async def rebuild_day(t: dt.date) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        await build_features(s, t)  # 內部 commit
    # 大盤脈絡缺當日時補建：market_daily 平時由 daily job 產生，手動重建若不補，
    # load_market_context 只接受當日資料，缺當日則大盤未知（market 成分排除、BUY 被擋）
    # （實際踩過：market_index 停在 09-04，舊版會用 4 天前的大盤脈絡算 09-07/08）。
    # 需要 market_index 有當日 TAIEX；沒有則 build_market_daily 自行略過。
    async with sm() as s:
        await build_market_daily(s, t)
    async with sm() as s:
        return await persist_signals(s, t)


async def rebuild(
    start: dt.date | None, end: dt.date | None, min_lookback: int
) -> None:
    # 回看視窗以「全部交易日曆」計算：是否足夠取決於資料起點，與重建區間無關。
    # 舊版在區間內再跳過前 N 天，只重建最近幾天時會一天都不做卻回報成功。
    calendar = await _trading_days(None, None)
    eligible = [
        d for d in calendar[min_lookback:]
        if (start is None or d >= start) and (end is None or d <= end)
    ]
    if not eligible:
        logger.warning(
            "無可重建交易日:全部 %d 天中前 %d 天為回看視窗,區間 %s～%s 內無符合日期",
            len(calendar), min_lookback, start or "起點", end or "最新",
        )
        return
    logger.info(
        "交易日曆共 %d 天,跳過前 %d 天回看視窗,區間內重建 %d 天分數",
        len(calendar), min_lookback, len(eligible),
    )
    for i, t in enumerate(eligible, 1):
        n = await rebuild_day(t)
        logger.info("[%d/%d] %s signal_snapshot=%d", i, len(eligible), t, n)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="逐日重建 composite → signal_snapshot")
    p.add_argument("--start", type=str, help="起始日 YYYY-MM-DD")
    p.add_argument("--end", type=str, help="結束日 YYYY-MM-DD")
    p.add_argument(
        "--min-lookback", type=int, default=20,
        help="跳過最早 N 個交易日(回看視窗不足,分數品質差;預設 20)",
    )
    return p.parse_args()


async def _amain() -> None:
    args = _parse_args()
    start = dt.date.fromisoformat(args.start) if args.start else None
    end = dt.date.fromisoformat(args.end) if args.end else None
    await rebuild(start, end, args.min_lookback)


if __name__ == "__main__":
    asyncio.run(_amain())
