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
    # load_market_context 會沿用「<= 當日的最新一筆」＝更早的 regime 去算分數
    # （實際踩過：market_index 停在 09-04，09-07/08 用 4 天前的大盤脈絡）。
    # 需要 market_index 有當日 TAIEX；沒有則 build_market_daily 自行略過。
    async with sm() as s:
        await build_market_daily(s, t)
    async with sm() as s:
        return await persist_signals(s, t)


async def rebuild(
    start: dt.date | None, end: dt.date | None, min_lookback: int
) -> None:
    days = await _trading_days(start, end)
    if len(days) <= min_lookback:
        logger.warning(
            "交易日僅 %d 天 <= min_lookback %d,無足夠回看視窗", len(days), min_lookback
        )
        return
    eligible = days[min_lookback:]
    logger.info(
        "交易日共 %d 天,跳過前 %d 天回看視窗,重建 %d 天分數",
        len(days), min_lookback, len(eligible),
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
