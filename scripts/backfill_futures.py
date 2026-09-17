"""回補台指期每日行情（TAIFEX futDataDown）。

期交所的每日行情下載支援日期區間、歷史查得到（與 TWT48U/TWTAVU 那類「只回未來」的
預告表不同），故 EOD 之前的日子都補得回來。以 daily_price 的交易日為基準，
只補 futures_daily 缺的月份區段（冪等，可重跑）。

台指期目前只用於概覽頁的大盤脈絡展示，不是評分成分——回補與否不影響 chip_score。

用法：
  APP_ENV=dev python -m scripts.backfill_futures --months 6
  APP_ENV=dev python -m scripts.backfill_futures --start 2026-04-01 --end 2026-09-17
  APP_ENV=dev python -m scripts.backfill_futures --months 6 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from sqlalchemy import select

from app.connectors import taifex as taifex_conn
from app.core.logging import get_logger
from app.db.models.market import DailyPrice, FuturesDaily
from app.db.session import get_sessionmaker
from app.importers.base import availability_for
from app.importers.service import import_futures

logger = get_logger("scripts.backfill_futures")

SLEEP_S = 1.2  # 期交所節流，與 backfill_history 一致
DEFAULT_MONTHS = 6


def _month_spans(days: list[dt.date]) -> list[tuple[dt.date, dt.date]]:
    """把缺漏交易日聚成「每月一段」——一次請求就能取回整月，不必逐日打。"""
    months = sorted({(d.year, d.month) for d in days})
    out = []
    for y, m in months:
        first = dt.date(y, m, 1)
        last = dt.date(y + (m == 12), (m % 12) + 1, 1) - dt.timedelta(days=1)
        out.append((first, last))
    return out


async def _missing_days(start: dt.date | None, end: dt.date | None) -> list[dt.date]:
    sm = get_sessionmaker()
    async with sm() as s:
        q = select(DailyPrice.data_date).distinct()
        if start:
            q = q.where(DailyPrice.data_date >= start)
        if end:
            q = q.where(DailyPrice.data_date <= end)
        price_days = {d for (d,) in (await s.execute(q)).all()}
        have = {
            d for (d,) in (await s.execute(select(FuturesDaily.data_date).distinct())).all()
        }
    # 盤中保護：同 backfill_sbl——尚未盤後的日子資料不完整，不匯入。
    now = dt.datetime.now()
    return sorted(d for d in (price_days - have) if availability_for(d) <= now)


async def run(start: dt.date | None, end: dt.date | None, dry_run: bool) -> int:
    days = await _missing_days(start, end)
    spans = _month_spans(days)
    logger.info(
        "台指期缺 %d 個交易日，聚成 %d 個月份區段%s",
        len(days), len(spans),
        "：" + ", ".join(f"{a}~{b}" for a, b in spans) if spans else "",
    )
    if dry_run or not spans:
        return 0

    sm = get_sessionmaker()
    total = 0
    for i, (a, b) in enumerate(spans, 1):
        try:
            csv_text = await taifex_conn.fetch_futures_daily(a, b)
            async with sm() as s:
                n = await import_futures(s, csv_text)
        except Exception as e:  # noqa: BLE001 — 單段失敗不中斷其餘
            logger.warning("[%d/%d] %s~%s 回補失敗：%s", i, len(spans), a, b, e)
            continue
        logger.info("[%d/%d] %s~%s：%d 筆", i, len(spans), a, b, n)
        total += n
        await asyncio.sleep(SLEEP_S)
    logger.info("台指期回補完成：共 %d 筆 / %d 段", total, len(spans))
    return total


def main() -> None:
    p = argparse.ArgumentParser(description="回補台指期每日行情（TAIFEX）")
    p.add_argument("--months", type=int, help=f"近 N 個月（預設 {DEFAULT_MONTHS}）")
    p.add_argument("--start", help="起始日 YYYY-MM-DD（覆蓋 --months）")
    p.add_argument("--end", help="結束日 YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true", help="只列出缺哪幾段")
    a = p.parse_args()
    start = dt.date.fromisoformat(a.start) if a.start else None
    if start is None:
        months = a.months or DEFAULT_MONTHS
        start = dt.date.today() - dt.timedelta(days=31 * months)
    asyncio.run(
        run(start, dt.date.fromisoformat(a.end) if a.end else None, a.dry_run)
    )


if __name__ == "__main__":
    main()
