"""回填 TWSE 歷史盤後行情(daily_price / institutional / margin)+ TAIEX 大盤脈絡。

backtest 驗證單調性需要「時間序列的每日 composite 分數」,而分數需要足夠深的
歷史(20 日回看視窗 + forward horizon)。此腳本逐交易日回填原始表,冪等可續跑。

交易日清單來自 FMTQIK(逐月),順便 import_index 建大盤脈絡。
對外抓取:每次請求後 sleep,失敗 retry,避免觸發 TWSE 速率限制。

用法:
  APP_ENV=dev python -m scripts.backfill_history --months 6
  APP_ENV=dev python -m scripts.backfill_history --months 6 --max-days 3   # 先小測
  APP_ENV=dev python -m scripts.backfill_history --start 2026-03-01 --end 2026-09-05
  APP_ENV=dev python -m scripts.backfill_history --months 6 --dry-run       # 只列交易日
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy import select

from app.connectors import twse as twse_conn
from app.core.logging import get_logger
from app.db.models.market import DailyPrice
from app.db.session import get_sessionmaker
from app.importers.service import (
    import_index,
    import_institutional,
    import_margin,
    import_ohlcv,
)
from app.importers.twse import parse_index

logger = get_logger("scripts.backfill")

T = TypeVar("T")


def _month_firsts(start: dt.date, end: dt.date) -> list[dt.date]:
    """涵蓋 [start, end] 的每個月 1 號(供 FMTQIK 逐月抓取)。"""
    out: list[dt.date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(dt.date(y, m, 1))
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


async def _with_retry(
    fn: Callable[[], Awaitable[T]], what: str, tries: int = 3, base_sleep: float = 5.0
) -> T:
    for i in range(tries):
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001 — 對外抓取,任何錯都重試
            wait = base_sleep * (i + 1)
            logger.warning("%s 失敗(第 %d/%d 次):%s;%.0fs 後重試", what, i + 1, tries, e, wait)
            await asyncio.sleep(wait)
    raise RuntimeError(f"{what} 重試 {tries} 次仍失敗")


async def collect_trading_days(
    start: dt.date, end: dt.date, sleep_s: float, do_index: bool
) -> list[dt.date]:
    """逐月抓 FMTQIK 取 [start,end] 交易日;do_index 時順便 import_index。"""
    sm = get_sessionmaker()
    days: set[dt.date] = set()
    for mstart in _month_firsts(start, end):
        raw = await _with_retry(
            lambda m=mstart: twse_conn.fetch_index_month(m), f"FMTQIK {mstart:%Y-%m}"
        )
        rows = parse_index(raw)
        for r in rows:
            if start <= r["data_date"] <= end:
                days.add(r["data_date"])
        if do_index and rows:
            async with sm() as s:
                await import_index(s, raw)
        logger.info("FMTQIK %s:交易日 %d 筆", f"{mstart:%Y-%m}", len(rows))
        await asyncio.sleep(sleep_s)
    return sorted(days)


async def _existing_price_dates(start: dt.date, end: dt.date) -> set[dt.date]:
    sm = get_sessionmaker()
    async with sm() as s:
        stmt = (
            select(DailyPrice.data_date)
            .where(DailyPrice.data_date >= start, DailyPrice.data_date <= end)
            .distinct()
        )
        return {d for (d,) in (await s.execute(stmt)).all()}


async def backfill(
    start: dt.date,
    end: dt.date,
    sleep_s: float,
    max_days: int | None,
    skip_existing: bool,
    do_index: bool,
    dry_run: bool,
) -> None:
    sm = get_sessionmaker()
    logger.info("回填範圍 %s ~ %s(sleep=%.1fs)", start, end, sleep_s)

    days = await collect_trading_days(start, end, sleep_s, do_index and not dry_run)
    logger.info("區間交易日共 %d 天", len(days))

    if skip_existing:
        existing = await _existing_price_dates(start, end)
        days = [d for d in days if d not in existing]
        logger.info("略過已存在 %d 天,待抓 %d 天", len(existing), len(days))

    if max_days is not None:
        days = days[:max_days]
        logger.info("--max-days 限制:本次只抓 %d 天", len(days))

    if dry_run:
        for d in days:
            logger.info("  [dry-run] %s", d)
        return

    for i, t in enumerate(days, 1):
        ohlcv = await _with_retry(lambda d=t: twse_conn.fetch_ohlcv(d), f"OHLCV {t}")
        await asyncio.sleep(sleep_s)
        inst = await _with_retry(
            lambda d=t: twse_conn.fetch_institutional(d), f"institutional {t}"
        )
        await asyncio.sleep(sleep_s)
        margin = await _with_retry(lambda d=t: twse_conn.fetch_margin(d), f"margin {t}")
        await asyncio.sleep(sleep_s)

        async with sm() as s:
            n_price = await import_ohlcv(s, ohlcv, t)
            n_inst = await import_institutional(s, inst, t)
            n_margin = await import_margin(s, margin, t)
        logger.info(
            "[%d/%d] %s price=%d inst=%d margin=%d",
            i, len(days), t, n_price, n_inst, n_margin,
        )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="回填 TWSE 歷史盤後行情")
    p.add_argument("--months", type=int, default=6, help="回填近 N 個月(預設 6)")
    p.add_argument("--start", type=str, help="起始日 YYYY-MM-DD(覆蓋 --months)")
    p.add_argument("--end", type=str, help="結束日 YYYY-MM-DD(預設今天)")
    p.add_argument("--sleep", type=float, default=2.5, help="每次請求後 sleep 秒數")
    p.add_argument("--max-days", type=int, help="只抓前 N 個交易日(測試用)")
    p.add_argument("--no-skip-existing", action="store_true", help="不略過已存在的日期")
    p.add_argument("--no-index", action="store_true", help="不抓/匯入 TAIEX")
    p.add_argument("--dry-run", action="store_true", help="只列交易日,不抓行情")
    return p.parse_args()


async def _amain() -> None:
    args = _parse_args()
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    if args.start:
        start = dt.date.fromisoformat(args.start)
    else:
        start = end - dt.timedelta(days=args.months * 31)
    await backfill(
        start=start,
        end=end,
        sleep_s=args.sleep,
        max_days=args.max_days,
        skip_existing=not args.no_skip_existing,
        do_index=not args.no_index,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    asyncio.run(_amain())
