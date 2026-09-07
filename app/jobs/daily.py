"""每日盤後 job：抓取 → 匯入 → 建特徵（見 docs/07 §19）。

用法：
  APP_ENV=dev python -m app.jobs.daily 2025-09-03
  APP_ENV=dev python -m app.jobs.daily 2025-09-03 --skip-import
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from app.connectors import tdcc as tdcc_conn
from app.connectors import twse as twse_conn
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.importers.service import (
    import_index,
    import_institutional,
    import_margin,
    import_ohlcv,
    import_sbl,
    import_tdcc,
)
from app.services.feature_builder import build_features
from app.services.market_score import build_market_daily
from app.services.signal_persist import persist_signals

logger = get_logger("jobs.daily")


def _month_starts(target: dt.date, months: int) -> list[dt.date]:
    """target 當月起往前 months 個月的每月 1 號（供 FMTQIK 逐月抓取）。"""
    out, y, m = [], target.year, target.month
    for _ in range(months):
        out.append(dt.date(y, m, 1))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


async def run(
    target: dt.date,
    do_import: bool = True,
    do_features: bool = True,
    do_signals: bool = True,
    do_tdcc: bool = False,
    do_index: bool = False,
) -> None:
    sm = get_sessionmaker()

    if do_index:
        # 抓近 4 個月 TAIEX（確保 MA60 有足夠歷史），再算大盤脈絡。
        logger.info("抓取 TAIEX 指數（近 4 個月）...")
        total = 0
        for mstart in _month_starts(target, 4):
            raw = await twse_conn.fetch_index_month(mstart)
            async with sm() as s:
                total += await import_index(s, raw)
        logger.info("TAIEX 匯入完成：%d 日", total)

    if do_tdcc:
        # TDCC openapi 只有當週快照（無日期參數）。
        logger.info("抓取 TDCC 股權分散（當週）...")
        records = await tdcc_conn.fetch_shareholder_distribution()
        async with sm() as s:
            nw, ns = await import_tdcc(s, records)
        logger.info("TDCC 匯入完成：weekly=%d summary=%d", nw, ns)

    if do_import:
        logger.info("抓取 TWSE 盤後資料 %s ...", target)
        ohlcv = await twse_conn.fetch_ohlcv(target)
        inst = await twse_conn.fetch_institutional(target)
        margin = await twse_conn.fetch_margin(target)
        async with sm() as s:
            n_price = await import_ohlcv(s, ohlcv, target)
            n_inst = await import_institutional(s, inst, target)
            n_margin = await import_margin(s, margin, target)
        # 借券 SBL（TWT93U）：新資料源，失敗不影響核心匯入。
        n_sbl = 0
        try:
            sbl = await twse_conn.fetch_sbl(target)
            async with sm() as s:
                n_sbl = await import_sbl(s, sbl, target)
        except Exception as e:  # noqa: BLE001 — SBL 非必要，缺則後續中性
            logger.warning("SBL 匯入失敗（TWT93U）：%s", e)
        logger.info(
            "匯入完成：price=%d institutional=%d margin=%d sbl=%d",
            n_price, n_inst, n_margin, n_sbl,
        )
        if n_price == 0:
            logger.warning("當日無 OHLCV（可能非交易日），略過建特徵。")
            return

    if do_features:
        async with sm() as s:
            n = await build_features(s, target)
        logger.info("特徵建立完成：feature_daily=%d", n)
        async with sm() as s:
            ok = await build_market_daily(s, target)
        logger.info("大盤脈絡：%s", "已建立" if ok else "略過（無 TAIEX 或非交易日）")

    if do_signals:
        # composite 分數落地(供 backtest / ML / 追蹤);前置為 feature_daily 已建。
        async with sm() as s:
            n_sig = await persist_signals(s, target)
        logger.info("訊號落地：signal_snapshot=%d", n_sig)


def main() -> None:
    p = argparse.ArgumentParser(description="每日盤後 import + feature job")
    p.add_argument("date", help="交易日 YYYY-MM-DD")
    p.add_argument("--skip-import", action="store_true")
    p.add_argument("--skip-features", action="store_true")
    p.add_argument("--skip-signals", action="store_true", help="不落地 signal_snapshot")
    p.add_argument("--tdcc", action="store_true", help="同時抓取當週 TDCC 股權分散")
    p.add_argument("--index", action="store_true", help="抓取 TAIEX 並建大盤脈絡")
    args = p.parse_args()
    target = dt.date.fromisoformat(args.date)
    asyncio.run(
        run(
            target,
            do_import=not args.skip_import,
            do_features=not args.skip_features,
            do_signals=not args.skip_signals,
            do_tdcc=args.tdcc,
            do_index=args.index,
        )
    )


if __name__ == "__main__":
    main()
