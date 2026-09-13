"""回補內部人持股轉讓事前申報（MOPS ajax_t56sb12，依日期查全市場；docs/14 §3）。

每個交易日 × 2 市場各一次請求，已涵蓋的日期略過。交易日曆取自 daily_price。
回補頁的「異動情形」可能是事後回寫的註記：parser 已拆成 amends_report_date / superseded_on，
特徵只在變更申報可用後才讓舊申報失效，不會 look-ahead。

用法：
  APP_ENV=dev python -m scripts.backfill_mops_transfers --start 2026-03-01 --end 2026-09-11
  APP_ENV=dev python -m scripts.backfill_mops_transfers --start 2026-09-01 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from sqlalchemy import distinct, select

from app.connectors import mops as conn
from app.core.logging import get_logger
from app.db.models.market import DailyPrice
from app.db.models.mops import MopsFetchCoverage
from app.db.session import get_sessionmaker
from app.importers import mops as parse
from app.importers.mops_service import import_transfers
from app.jobs.mops import local_today

logger = get_logger("scripts.backfill_mops_transfers")
MARKETS = (parse.MARKET_TWSE, parse.MARKET_TPEX)


async def _todo(start: dt.date, end: dt.date) -> list[tuple[dt.date, str]]:
    async with get_sessionmaker()() as s:
        days = (await s.execute(
            select(distinct(DailyPrice.data_date))
            .where(DailyPrice.data_date >= start, DailyPrice.data_date <= end)
            .order_by(DailyPrice.data_date)
        )).scalars().all()
        done = set((await s.execute(
            select(MopsFetchCoverage.data_date, MopsFetchCoverage.market).where(
                MopsFetchCoverage.dataset == parse.DATASET_TRANSFER,
                MopsFetchCoverage.scope_key == parse.SCOPE_ALL,
                MopsFetchCoverage.data_date >= start, MopsFetchCoverage.data_date <= end,
            )
        )).all())
    return [(d, mk) for d in days for mk in MARKETS if (d, mk) not in done]


async def run(start: dt.date, end: dt.date, dry_run: bool) -> int:
    todo = await _todo(start, end)
    logger.info("待抓 %d 組（交易日 × 市場），預估 %.1f 分鐘", len(todo),
                len(todo) * (conn.throttle_sec() + 1) / 60)
    if dry_run or not todo:
        return 0
    total, failed = 0, 0
    for i, (d, mk) in enumerate(todo, 1):
        try:
            html = await conn.fetch_transfer_page(d, mk)
            batch = parse.parse_transfer_page(html, mk, d)
            async with get_sessionmaker()() as s:
                total += await import_transfers(s, batch, mk, parse.SOURCE_WEB,
                                                mode=parse.MODE_BACKFILL)
        except conn.MopsBlockedError:
            logger.error("MOPS 回傳安全性阻擋頁，停止回補（已完成 %d/%d）", i - 1, len(todo))
            raise
        except Exception as e:  # noqa: BLE001 — 單日失敗不寫涵蓋紀錄，下次重跑會再補
            failed += 1
            logger.warning("[%d/%d] %s %s 失敗：%s: %s", i, len(todo), d, mk, type(e).__name__, e)
        if i % 20 == 0 or i == len(todo):
            logger.info("[%d/%d] 已處理 %s %s", i, len(todo), d, mk)
        await asyncio.sleep(conn.throttle_sec())
    logger.info("轉讓申報回補完成：%d 筆，失敗 %d 組", total, failed)
    if failed:
        raise SystemExit(f"{failed} 組抓取失敗，涵蓋不完整（相關窗內轉讓特徵將為 NULL）")
    return total


def main() -> None:
    p = argparse.ArgumentParser(description="回補 MOPS 內部人持股轉讓事前申報")
    p.add_argument("--start", required=True, help="起始日 YYYY-MM-DD")
    p.add_argument("--end", help="結束日 YYYY-MM-DD（預設昨天）")
    p.add_argument("--dry-run", action="store_true", help="只列待抓組數")
    a = p.parse_args()
    end = dt.date.fromisoformat(a.end) if a.end else local_today() - dt.timedelta(days=1)
    asyncio.run(run(dt.date.fromisoformat(a.start), end, a.dry_run))


if __name__ == "__main__":
    main()
