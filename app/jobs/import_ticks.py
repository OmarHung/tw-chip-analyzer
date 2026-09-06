"""批次逐筆匯入 job：對當日成交金額前 N 檔抓 Shioaji 逐筆並存入 raw_tick。

範圍 config 化（config/thresholds.yaml `intraday_batch`），節流 + 容錯 + 可續跑：
- 只抓 turnover >= min_turnover 的前 max_symbols 檔（避免 Shioaji 資料配額爆量）。
- 每檔之間節流 throttle_ms；單檔失敗只記錄不中斷。
- 監看 api.usage()，用量達 usage_stop_pct% 即停止並記錄已處理/剩餘。
- get_ticks 本身 DB 快取優先，已抓過的當日不會重打 Shioaji（可續跑）。

用法：
  APP_ENV=dev python -m app.jobs.import_ticks 2026-09-04
  APP_ENV=dev python -m app.jobs.import_ticks 2026-09-04 --max-symbols 100
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.shioaji_market import usage_sync
from app.core.config import get_thresholds
from app.core.logging import get_logger
from app.db.models.market import DailyPrice
from app.db.session import get_sessionmaker
from app.services.ticks import get_ticks

logger = get_logger("jobs.import_ticks")


async def _target_symbols(
    session: AsyncSession, target: dt.date, min_turnover: float, max_symbols: int
) -> list[str]:
    """當日 turnover >= 門檻、由高到低的前 max_symbols 檔。"""
    stmt = (
        select(DailyPrice.symbol, DailyPrice.turnover)
        .where(DailyPrice.data_date == target, DailyPrice.turnover >= min_turnover)
        .order_by(DailyPrice.turnover.desc())
        .limit(max_symbols)
    )
    return [sym for sym, _ in (await session.execute(stmt)).all()]


async def run(target: dt.date, max_symbols: int | None = None) -> None:
    cfg = get_thresholds().intraday_batch
    min_turnover = float(cfg.get("min_turnover", 0))
    max_symbols = max_symbols or int(cfg.get("max_symbols", 200))
    throttle = float(cfg.get("throttle_ms", 200)) / 1000.0
    stop_pct = float(cfg.get("usage_stop_pct", 95))

    sm = get_sessionmaker()
    async with sm() as session:
        symbols = await _target_symbols(session, target, min_turnover, max_symbols)
    if not symbols:
        logger.warning("當日無符合條件標的（date=%s min_turnover=%s）", target, min_turnover)
        return
    logger.info(
        "批次逐筆匯入 %s：目標 %d 檔（turnover>=%s，上限 %d）",
        target, len(symbols), min_turnover, max_symbols,
    )

    u0 = usage_sync()
    if u0 and u0["used_pct"] is not None:
        logger.info("Shioaji 起始用量：%.1f%%", u0["used_pct"])

    done = fetched = failed = 0
    stopped = False
    async with sm() as session:
        for i, sym in enumerate(symbols):
            try:
                ticks = await get_ticks(session, sym, target)
                done += 1
                if ticks:
                    fetched += 1
            except Exception as e:  # noqa: BLE001 — 單檔失敗不中斷批次
                failed += 1
                logger.warning("逐筆匯入失敗 %s：%s", sym, e)

            if (i + 1) % 25 == 0:
                u = usage_sync()
                pct = u["used_pct"] if u else None
                logger.info(
                    "進度 %d/%d（fetched=%d failed=%d）用量=%s",
                    i + 1, len(symbols), fetched, failed,
                    f"{pct:.1f}%" if pct is not None else "n/a",
                )
                if pct is not None and pct >= stop_pct:
                    logger.warning("Shioaji 用量達 %.1f%% >= %s%%，停止批次。", pct, stop_pct)
                    stopped = True
                    break

            if throttle:
                await asyncio.sleep(throttle)

    skipped = len(symbols) - done
    logger.info(
        "批次完成%s：處理=%d 有逐筆=%d 失敗=%d 略過=%d（目標 %d）",
        "（提前停止）" if stopped else "", done, fetched, failed, skipped, len(symbols),
    )


def main() -> None:
    p = argparse.ArgumentParser(description="批次逐筆匯入（前 N 檔）")
    p.add_argument("date", help="交易日 YYYY-MM-DD")
    p.add_argument("--max-symbols", type=int, default=None, help="覆寫 config max_symbols")
    args = p.parse_args()
    asyncio.run(run(dt.date.fromisoformat(args.date), max_symbols=args.max_symbols))


if __name__ == "__main__":
    main()
