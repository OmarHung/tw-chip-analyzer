"""逐日重建 Phase 2 MOPS shadow 特徵（只寫 mops_shadow_feature_daily；docs/14 §6）。

不動 feature_daily / signal_snapshot / market_daily，正式分數與建議不受影響。

用法：
  APP_ENV=dev python -m scripts.rebuild_mops_features --start 2026-03-01 --end 2026-09-11
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from sqlalchemy import distinct, select

from app.core.logging import get_logger
from app.db.models.market import DailyPrice
from app.db.session import get_sessionmaker
from app.services.mops_features import build_mops_features

logger = get_logger("scripts.rebuild_mops_features")


async def run(start: dt.date | None, end: dt.date | None) -> int:
    async with get_sessionmaker()() as s:
        stmt = select(distinct(DailyPrice.data_date)).order_by(DailyPrice.data_date)
        if start:
            stmt = stmt.where(DailyPrice.data_date >= start)
        if end:
            stmt = stmt.where(DailyPrice.data_date <= end)
        days = (await s.execute(stmt)).scalars().all()
    logger.info("重建 MOPS shadow 特徵：%d 個交易日", len(days))
    total = 0
    for i, d in enumerate(days, 1):
        async with get_sessionmaker()() as s:
            total += (await build_mops_features(s, d)).rows
        if i % 20 == 0 or i == len(days):
            logger.info("[%d/%d] %s", i, len(days), d)
    logger.info("完成：%d 列", total)
    return total


def main() -> None:
    p = argparse.ArgumentParser(description="重建 Phase 2 MOPS shadow 特徵（不影響正式分數）")
    p.add_argument("--start", help="起始日 YYYY-MM-DD")
    p.add_argument("--end", help="結束日 YYYY-MM-DD")
    a = p.parse_args()
    asyncio.run(run(
        dt.date.fromisoformat(a.start) if a.start else None,
        dt.date.fromisoformat(a.end) if a.end else None,
    ))


if __name__ == "__main__":
    main()
