"""每日盤後 job：抓取 → 匯入 → 建特徵（見 docs/07 §19）。

用法：
  APP_ENV=dev python -m app.jobs.daily 2025-09-03
  APP_ENV=dev python -m app.jobs.daily 2025-09-03 --skip-import
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from app.connectors import twse as twse_conn
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.importers.service import import_institutional, import_margin, import_ohlcv
from app.services.feature_builder import build_features

logger = get_logger("jobs.daily")


async def run(target: dt.date, do_import: bool = True, do_features: bool = True) -> None:
    sm = get_sessionmaker()

    if do_import:
        logger.info("抓取 TWSE 盤後資料 %s ...", target)
        ohlcv = await twse_conn.fetch_ohlcv(target)
        inst = await twse_conn.fetch_institutional(target)
        margin = await twse_conn.fetch_margin(target)
        async with sm() as s:
            n_price = await import_ohlcv(s, ohlcv, target)
            n_inst = await import_institutional(s, inst, target)
            n_margin = await import_margin(s, margin, target)
        logger.info(
            "匯入完成：price=%d institutional=%d margin=%d",
            n_price, n_inst, n_margin,
        )
        if n_price == 0:
            logger.warning("當日無 OHLCV（可能非交易日），略過建特徵。")
            return

    if do_features:
        async with sm() as s:
            n = await build_features(s, target)
        logger.info("特徵建立完成：feature_daily=%d", n)


def main() -> None:
    p = argparse.ArgumentParser(description="每日盤後 import + feature job")
    p.add_argument("date", help="交易日 YYYY-MM-DD")
    p.add_argument("--skip-import", action="store_true")
    p.add_argument("--skip-features", action="store_true")
    args = p.parse_args()
    target = dt.date.fromisoformat(args.date)
    asyncio.run(
        run(target, do_import=not args.skip_import, do_features=not args.skip_features)
    )


if __name__ == "__main__":
    main()
