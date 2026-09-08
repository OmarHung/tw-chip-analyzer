"""一次性回補除權除息事件（TWT49U，區間查詢）。

TWT49U 支援 startDate/endDate 區間查詢且歷史可取（與 SBL 不同），故可一次補整段，
不必逐日跑 EOD。以月為單位分段查詢，避免單次區間過長。

用法：
  APP_ENV=dev  python -m scripts.backfill_corporate_actions 2026-06-01 2026-09-08
  APP_ENV=prod python -m scripts.backfill_corporate_actions 2026-06-01 2026-09-08

回補後若要讓既有 feature_daily 反映還原價，需重建該區間特徵（見 scripts/rebuild_signals
或逐日 daily --skip-import）。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from app.connectors import twse as twse_conn
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.importers.service import import_ex_dividend

logger = get_logger("scripts.backfill_ca")


def _month_ranges(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    """把 [start,end] 切成不跨月的區段。"""
    out: list[tuple[dt.date, dt.date]] = []
    cur = start
    while cur <= end:
        if cur.month == 12:
            nxt = dt.date(cur.year + 1, 1, 1)
        else:
            nxt = dt.date(cur.year, cur.month + 1, 1)
        seg_end = min(end, nxt - dt.timedelta(days=1))
        out.append((cur, seg_end))
        cur = nxt
    return out


async def run(start: dt.date, end: dt.date) -> int:
    sm = get_sessionmaker()
    total = 0
    for seg_start, seg_end in _month_ranges(start, end):
        raw = await twse_conn.fetch_ex_dividend(seg_start, seg_end)
        async with sm() as s:
            n = await import_ex_dividend(s, raw)
        logger.info("除權除息回補 %s~%s：%d 筆", seg_start, seg_end, n)
        total += n
    logger.info("除權除息回補完成：共 %d 筆（%s~%s）", total, start, end)
    return total


def main() -> None:
    p = argparse.ArgumentParser(description="一次性回補除權除息事件（TWT49U）")
    p.add_argument("start", help="起始日 YYYY-MM-DD")
    p.add_argument("end", help="結束日 YYYY-MM-DD")
    args = p.parse_args()
    asyncio.run(run(dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)))


if __name__ == "__main__":
    main()
