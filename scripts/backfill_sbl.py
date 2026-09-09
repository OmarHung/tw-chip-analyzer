"""回補借券 SBL（TWT93U）缺漏日。

TWT93U 歷史單日可查（2026-09-09 實測證實），故 EOD 中斷造成的缺口都補得回來。
以 daily_price 的交易日為基準，只補 sbl_daily 沒有的日子（冪等，可重跑）。

SBL 樣本數直接決定 `scripts/sbl_factor_oos.py` 能不能給出有意義的 t 值
（該腳本有效 test 橫斷面日 ≈ N/2 − 30，需 N≈100~120 交易日），故缺口要補齊再驗。

用法：
  APP_ENV=dev  python -m scripts.backfill_sbl                       # 補全部缺漏
  APP_ENV=dev  python -m scripts.backfill_sbl --start 2026-05-01
  APP_ENV=dev  python -m scripts.backfill_sbl --dry-run             # 只列缺哪幾天

回補後 sbl_change_z 要重算才會進 feature_daily：
  APP_ENV=dev python -m scripts.rebuild_signals --start <缺口首日>
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from sqlalchemy import select

from app.connectors import twse as twse_conn
from app.core.logging import get_logger
from app.db.models.chips import SblDaily
from app.db.models.market import DailyPrice
from app.db.session import get_sessionmaker
from app.importers.base import availability_for
from app.importers.service import import_sbl

logger = get_logger("scripts.backfill_sbl")

SLEEP_S = 1.2  # TWSE 節流，與 backfill_history 一致


async def _missing_days(start: dt.date | None, end: dt.date | None) -> list[dt.date]:
    sm = get_sessionmaker()
    async with sm() as s:
        q = select(DailyPrice.data_date).distinct()
        if start:
            q = q.where(DailyPrice.data_date >= start)
        if end:
            q = q.where(DailyPrice.data_date <= end)
        price_days = {d for (d,) in (await s.execute(q)).all()}
        have = {d for (d,) in (await s.execute(select(SblDaily.data_date).distinct())).all()}
    # 盤中保護:同 backfill_history——尚未盤後的日子資料不完整,不匯入。
    now = dt.datetime.now()
    return sorted(d for d in (price_days - have) if availability_for(d) <= now)


async def run(start: dt.date | None, end: dt.date | None, dry_run: bool) -> int:
    days = await _missing_days(start, end)
    logger.info("SBL 缺 %d 個交易日%s", len(days), "：" + ", ".join(map(str, days)) if days else "")
    if dry_run or not days:
        return 0

    sm = get_sessionmaker()
    total = 0
    for i, d in enumerate(days, 1):
        try:
            raw = await twse_conn.fetch_sbl(d)
            async with sm() as s:
                n = await import_sbl(s, raw, d)
        except Exception as e:  # noqa: BLE001 — 單日失敗不中斷其餘
            logger.warning("[%d/%d] %s 回補失敗：%s", i, len(days), d, e)
            continue
        logger.info("[%d/%d] %s：%d 筆", i, len(days), d, n)
        total += n
        await asyncio.sleep(SLEEP_S)
    logger.info("SBL 回補完成：共 %d 筆 / %d 天", total, len(days))
    return total


def main() -> None:
    p = argparse.ArgumentParser(description="回補 SBL 借券（TWT93U）缺漏日")
    p.add_argument("--start", help="起始日 YYYY-MM-DD（預設不限）")
    p.add_argument("--end", help="結束日 YYYY-MM-DD（預設不限）")
    p.add_argument("--dry-run", action="store_true", help="只列出缺哪幾天")
    a = p.parse_args()
    asyncio.run(
        run(
            dt.date.fromisoformat(a.start) if a.start else None,
            dt.date.fromisoformat(a.end) if a.end else None,
            a.dry_run,
        )
    )


if __name__ == "__main__":
    main()
