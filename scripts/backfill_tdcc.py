"""回補 TDCC 股權分散歷史週次（集保個股查詢頁，可回溯約 51 週）。

為什麼需要：openapi 1-5 只給當週快照，`feature_daily` 的 holder 分項要有 ≥2 週
才能算真實 week-over-week change，而 backtest 要驗證 holder 因子更需要歷史。
集保查詢頁是唯一免費的歷史來源，但**逐檔逐週**（一次一檔一週，回應約 60KB）：

    全市場 2954 檔 × 51 週 ≈ 15 萬次請求 / 約 10 GB / 12+ 小時 → 不建議
    前 300 檔       × 51 週 ≈ 1.5 萬次請求 / 約 1 GB   / 約 1.3 小時 → 可行

故預設只補「近期成交值前 N 檔」。**注意**：部分回補會讓一部分標的有 TDCC、一部分
沒有；`analysis.score_features` 已把「無 TDCC」視為缺成分排除，並由
`analyze_market` 依成分組合分組做百分位，不會產生混排偏差。

用法：
  APP_ENV=dev python -m scripts.backfill_tdcc --top 300 --weeks 51
  APP_ENV=dev python -m scripts.backfill_tdcc --symbols 2330,2317 --weeks 8
  APP_ENV=dev python -m scripts.backfill_tdcc --top 50 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from sqlalchemy import func, select

from app.connectors.tdcc import TdccWebClient
from app.core.logging import get_logger
from app.db.models.chips import TdccSummaryWeekly
from app.db.models.market import DailyPrice
from app.db.session import get_sessionmaker
from app.importers.service import import_tdcc
from app.importers.tdcc import parse_stock_page

logger = get_logger("scripts.backfill_tdcc")

SLEEP_S = 0.3  # 集保是公務網站，保守節流


async def _top_symbols(limit: int) -> list[str]:
    """近 20 個交易日平均成交值前 limit 檔（回補優先序）。"""
    sm = get_sessionmaker()
    async with sm() as s:
        last = (
            await s.execute(select(func.max(DailyPrice.data_date)))
        ).scalar_one_or_none()
        if last is None:
            return []
        start = last - dt.timedelta(days=30)
        rows = (
            await s.execute(
                select(DailyPrice.symbol, func.avg(DailyPrice.turnover).label("t"))
                .where(DailyPrice.data_date >= start)
                .group_by(DailyPrice.symbol)
                .order_by(func.avg(DailyPrice.turnover).desc())
                .limit(limit)
            )
        ).all()
    return [r[0] for r in rows]


async def _existing(symbols: set[str]) -> set[tuple[str, dt.date]]:
    sm = get_sessionmaker()
    async with sm() as s:
        rows = (
            await s.execute(
                select(TdccSummaryWeekly.symbol, TdccSummaryWeekly.data_date).where(
                    TdccSummaryWeekly.symbol.in_(list(symbols))
                )
            )
        ).all()
    return {(a, b) for a, b in rows}


async def run(symbols: list[str], weeks: int, dry_run: bool) -> int:
    sm = get_sessionmaker()
    total = 0
    async with TdccWebClient() as client:
        dates = client.available_dates[:weeks]
        logger.info(
            "可回補週次 %d（取最近 %d 週：%s ~ %s）；標的 %d 檔",
            len(client.available_dates), len(dates),
            dates[-1] if dates else "-", dates[0] if dates else "-", len(symbols),
        )
        done = await _existing(set(symbols))
        todo = [
            (sym, d) for d in dates for sym in symbols
            if (sym, dt.datetime.strptime(d, "%Y%m%d").date()) not in done
        ]
        logger.info(
            "待抓 %d 組（已有 %d 組，略過）；預估 %.1f 分鐘",
            len(todo), len(symbols) * len(dates) - len(todo),
            len(todo) * (SLEEP_S + 0.1) / 60,
        )
        if dry_run or not todo:
            return 0

        # 以「週」為單位累積 records 後一次匯入（同週共用一次 upsert）
        by_week: dict[str, list[dict]] = {}
        for i, (sym, d) in enumerate(todo, 1):
            data_date = dt.datetime.strptime(d, "%Y%m%d").date()
            try:
                html = await client.fetch_stock(sym, d)
                recs = parse_stock_page(html, sym, data_date)
            except Exception as e:  # noqa: BLE001 — 單檔失敗不中斷整批
                logger.warning("[%d/%d] %s %s 失敗：%s", i, len(todo), sym, d, e)
                continue
            if recs:
                by_week.setdefault(d, []).extend(recs)
            if i % 200 == 0 or i == len(todo):
                logger.info("[%d/%d] 已抓 %s %s", i, len(todo), sym, d)
            await asyncio.sleep(SLEEP_S)

        for d, recs in sorted(by_week.items()):
            async with sm() as s:
                nw, ns = await import_tdcc(s, recs)
            logger.info("匯入 %s：weekly=%d summary=%d", d, nw, ns)
            total += ns
    logger.info("TDCC 回補完成：%d 檔週資料", total)
    return total


def main() -> None:
    p = argparse.ArgumentParser(description="回補 TDCC 股權分散歷史週次")
    p.add_argument("--top", type=int, default=300, help="近期成交值前 N 檔（預設 300）")
    p.add_argument("--symbols", help="指定代號，逗號分隔（覆蓋 --top）")
    p.add_argument("--weeks", type=int, default=51, help="回補最近 N 週（預設 51＝全部）")
    p.add_argument("--dry-run", action="store_true", help="只列出待抓組數與預估耗時")
    a = p.parse_args()

    async def _amain() -> None:
        symbols = (
            [x.strip() for x in a.symbols.split(",") if x.strip()]
            if a.symbols
            else await _top_symbols(a.top)
        )
        if not symbols:
            logger.warning("沒有可回補的標的（daily_price 是空的？）")
            return
        await run(symbols, a.weeks, a.dry_run)

    asyncio.run(_amain())


if __name__ == "__main__":
    main()
