"""一次性回補公司行動事件（區間查詢）：

- 除權除息 TWT49U（除權/除息）
- 面額變更/拆股 TWTB8U（如 6949 面額 1:20 變更）
- 減資 TWTAUU（退還股款/彌補虧損）

三者皆支援 startDate/endDate 區間查詢且歷史可取（與 SBL 不同），故可一次補整段，
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
from app.importers.service import (
    import_capital_reduction,
    import_ex_dividend,
    import_ex_rights_forecast,
    import_par_change,
)

logger = get_logger("scripts.backfill_ca")

# (標籤, connector 抓取, importer)
_SOURCES = (
    ("除權息", twse_conn.fetch_ex_dividend, import_ex_dividend),
    ("面額變更", twse_conn.fetch_par_change, import_par_change),
    ("減資", twse_conn.fetch_capital_reduction, import_capital_reduction),
)


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
    # 預告表 TWT48U 只回「未來尚未執行」的事件（區間參數無效），故不進月份迴圈，
    # 只跑一次把即將到來的配股率（量還原因子）補上。歷史配股率補不回來。
    try:
        raw = await twse_conn.fetch_ex_rights_forecast()
        async with sm() as s:
            n = await import_ex_rights_forecast(s, raw)
        logger.info("除權息預告回補（未來事件配股率）：%d 筆", n)
        total += n
    except Exception as e:  # noqa: BLE001 — 同其他來源，失敗不中斷
        logger.warning("除權息預告回補失敗：%s", e)

    for seg_start, seg_end in _month_ranges(start, end):
        for label, fetch, imp in _SOURCES:
            try:
                raw = await fetch(seg_start, seg_end)
                async with sm() as s:
                    n = await imp(s, raw)
            except Exception as e:  # noqa: BLE001 — 單一來源失敗不中斷其餘
                logger.warning("%s 回補失敗 %s~%s：%s", label, seg_start, seg_end, e)
                continue
            logger.info("%s回補 %s~%s：%d 筆", label, seg_start, seg_end, n)
            total += n
    logger.info("公司行動回補完成：共 %d 筆（%s~%s）", total, start, end)
    return total


def main() -> None:
    p = argparse.ArgumentParser(description="一次性回補公司行動事件（除權息/面額變更/減資）")
    p.add_argument("start", help="起始日 YYYY-MM-DD")
    p.add_argument("end", help="結束日 YYYY-MM-DD")
    args = p.parse_args()
    asyncio.run(run(dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)))


if __name__ == "__main__":
    main()
