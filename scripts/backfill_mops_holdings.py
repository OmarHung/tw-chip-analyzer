"""回補董監／內部人／大股東持股與質押（MOPS ajax_stapap1，逐公司逐月；docs/14 §2）。

OpenAPI 只有最新一期，歷史只能逐公司逐月查網頁：全市場約 1,800 檔 × 12 月 ≈ 2.2 萬次請求，
故預設只補近期成交值前 N 檔（config mops.backfill）。

**修正版 look-ahead 限制（不可消除）**：網頁顯示查詢當下內容，事後更正過的值會被標上推定揭露時點；
一律標 source=mops_web，驗證時分 source 報告。誠實 OOS 以每日 OpenAPI 向前累積為準。

用法：
  APP_ENV=dev python -m scripts.backfill_mops_holdings --top 300 --months 12
  APP_ENV=dev python -m scripts.backfill_mops_holdings --symbols 2330,5386 --months 3
  APP_ENV=dev python -m scripts.backfill_mops_holdings --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import datetime as dt

from sqlalchemy import func, select

from app.connectors import mops as conn
from app.core.config import get_thresholds
from app.core.logging import get_logger
from app.db.models.market import DailyPrice, Stock
from app.db.models.mops import InsiderHoldingMonthly, MopsFetchCoverage
from app.db.session import get_sessionmaker
from app.importers import mops as parse
from app.importers.mops_service import import_holdings_page
from app.jobs.mops import local_today

logger = get_logger("scripts.backfill_mops_holdings")
_MARKETS = {parse.MARKET_TWSE, parse.MARKET_TPEX}


def available_months(today: dt.date, months: int) -> list[dt.date]:
    """最近 months 個「推定揭露日已過」的月份（月底），新到舊。"""
    out: list[dt.date] = []
    y, m = today.year, today.month
    while len(out) < months:
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        end = dt.date(y, m, calendar.monthrange(y, m)[1])
        if parse.holding_available_at(end, None).date() <= today:
            out.append(end)
    return out


async def _targets(top: int, symbols: list[str] | None) -> list[tuple[str, str]]:
    async with get_sessionmaker()() as s:
        if symbols:
            rows = (await s.execute(
                select(Stock.symbol, Stock.market).where(Stock.symbol.in_(symbols))
            )).all()
        else:
            last = (await s.execute(select(func.max(DailyPrice.data_date)))).scalar_one_or_none()
            if last is None:
                return []
            rows = (await s.execute(
                select(Stock.symbol, Stock.market)
                .join(DailyPrice, DailyPrice.symbol == Stock.symbol)
                .where(DailyPrice.data_date >= last - dt.timedelta(days=30))
                .group_by(Stock.symbol, Stock.market)
                .order_by(func.avg(DailyPrice.turnover).desc())
                .limit(top)
            )).all()
    return [(sym, mk) for sym, mk in rows if mk in _MARKETS]


async def _existing(symbols: list[str]) -> set[tuple[str, dt.date]]:
    """已成功抓過的 (symbol, 月)：持股涵蓋紀錄（含查無資料頁）∪ 涵蓋紀錄導入前已存的網頁列。"""
    async with get_sessionmaker()() as s:
        covered = (await s.execute(
            select(MopsFetchCoverage.scope_key, MopsFetchCoverage.data_date).where(
                MopsFetchCoverage.dataset == parse.DATASET_HOLDING,
                MopsFetchCoverage.scope_key.in_(symbols),
            )
        )).all()
        rows = (await s.execute(
            select(InsiderHoldingMonthly.symbol, InsiderHoldingMonthly.data_date).where(
                InsiderHoldingMonthly.symbol.in_(symbols),
                InsiderHoldingMonthly.source == parse.SOURCE_WEB,
            ).distinct()
        )).all()
    return set(covered) | set(rows)


async def run(top: int, months: int, symbols: list[str] | None, dry_run: bool) -> int:
    targets = await _targets(top, symbols)
    month_ends = available_months(local_today(), months)
    done = await _existing([s for s, _ in targets])
    todo = [(sym, mk, me) for me in month_ends for sym, mk in targets if (sym, me) not in done]
    logger.info("標的 %d 檔 × %d 月，待抓 %d 組，預估 %.1f 分鐘", len(targets), len(month_ends),
                len(todo), len(todo) * (conn.throttle_sec() + 1) / 60)
    if dry_run or not todo:
        return 0
    total, empty, failed = 0, 0, 0
    for i, (sym, mk, me) in enumerate(todo, 1):
        try:
            html = await conn.fetch_holdings_page(sym, mk, me.year, me.month)
            rows = parse.parse_holdings_page(html, sym, mk)
            async with get_sessionmaker()() as s:
                # 成功解析才寫（含零筆頁的涵蓋）；阻擋/解析錯誤/年月不符在此之前拋出，不寫涵蓋
                total += await import_holdings_page(
                    s, rows, symbol=sym, market=mk, month_end=me, mode=parse.MODE_BACKFILL,
                )
            if not rows:
                empty += 1
        except conn.MopsBlockedError:
            logger.error("MOPS 回傳安全性阻擋頁，停止回補（已完成 %d/%d）", i - 1, len(todo))
            raise
        except Exception as e:  # noqa: BLE001 — 單檔單月失敗不中斷整批
            failed += 1
            logger.warning("[%d/%d] %s %s 失敗：%s: %s", i, len(todo), sym, me, type(e).__name__, e)
        if i % 100 == 0 or i == len(todo):
            logger.info("[%d/%d] 已處理 %s %s", i, len(todo), sym, me)
        await asyncio.sleep(conn.throttle_sec())
    logger.info("持股回補完成：%d 列，查無資料 %d 組，失敗 %d 組", total, empty, failed)
    return total


def main() -> None:
    cfg = get_thresholds().get("mops", "backfill", default={}) or {}
    p = argparse.ArgumentParser(description="回補 MOPS 董監/內部人持股與質押（逐公司逐月）")
    p.add_argument("--top", type=int, default=int(cfg.get("holdings_top", 300)))
    p.add_argument("--months", type=int, default=int(cfg.get("holdings_months", 12)))
    p.add_argument("--symbols", help="指定代號，逗號分隔（覆蓋 --top）")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    symbols = [x.strip() for x in a.symbols.split(",") if x.strip()] if a.symbols else None
    asyncio.run(run(a.top, a.months, symbols, a.dry_run))


if __name__ == "__main__":
    main()
