"""生產資料升級:TPEx 歷史回填 + 全歷史 feature/signal 重建(冪等,可重跑)。

用於部署「percentile 映射 + TPEx 接入」後,把既有歷史升級成一致狀態:
1. 對 DB 內每個既有交易日回填 TPEx(行情/法人/融資券;節流 + 重試)
2. 全部交易日重建 feature_daily + signal_snapshot(含上櫃橫斷面、百分位分數)

用法(容器內):
    python -m scripts.prod_data_upgrade            # 兩階段都跑
    python -m scripts.prod_data_upgrade --skip-tpex  # 只重建(TPEx 已回填過)
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.connectors import tpex as tpex_conn
from app.db.session import get_sessionmaker
from app.importers.service import (
    import_tpex_institutional,
    import_tpex_margin,
    import_tpex_ohlcv,
)
from app.jobs.daily import run as daily_run

THROTTLE = 1.2  # 秒/請求;TPEx 未見嚴格限流,保守節流


async def _retry(fn, d, tries=3):
    delay = 3.0
    for k in range(tries):
        try:
            return await fn(d)
        except Exception:
            if k == tries - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2


async def _dates() -> list:
    async with get_sessionmaker()() as s:
        return [
            r[0]
            for r in (
                await s.execute(text(
                    "select distinct data_date from daily_price "
                    "where data_date >= '2026-01-01' order by data_date"
                ))
            ).all()
        ]


async def backfill_tpex(dates: list) -> None:
    ok = fail = 0
    for i, d in enumerate(dates):
        try:
            q = await _retry(tpex_conn.fetch_ohlcv, d)
            await asyncio.sleep(THROTTLE)
            ins = await _retry(tpex_conn.fetch_institutional, d)
            await asyncio.sleep(THROTTLE)
            mg = await _retry(tpex_conn.fetch_margin, d)
            await asyncio.sleep(THROTTLE)
            async with get_sessionmaker()() as s:
                await import_tpex_ohlcv(s, q, d)
                await import_tpex_institutional(s, ins, d)
                await import_tpex_margin(s, mg, d)
            ok += 1
        except Exception as e:  # noqa: BLE001 — 單日失敗續跑
            fail += 1
            print(f"  fail {d} {e!r}"[:120], flush=True)
        if (i + 1) % 20 == 0:
            print(f"  tpex {i + 1}/{len(dates)} ok={ok} fail={fail}", flush=True)
    print(f"TPEX BACKFILL DONE ok={ok} fail={fail}/{len(dates)}", flush=True)


async def rebuild_all(dates: list) -> None:
    for i, d in enumerate(dates):
        await daily_run(d, do_import=False, do_features=True, do_signals=True)
        if (i + 1) % 20 == 0:
            print(f"  rebuild {i + 1}/{len(dates)}", flush=True)
    print(f"REBUILD DONE {len(dates)}", flush=True)


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--skip-tpex", action="store_true")
    args = p.parse_args()
    dates = await _dates()
    print(f"既有交易日 {len(dates)}({dates[0]} ~ {dates[-1]})", flush=True)
    if not args.skip_tpex:
        await backfill_tpex(dates)
    await rebuild_all(dates)
    print("PROD DATA UPGRADE DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
