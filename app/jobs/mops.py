"""Phase 2 MOPS 每日 job（shadow-only，見 docs/14 §8）。

流程：OpenAPI 最新持股（上市+上櫃）→ 指定日轉讓事前申報（網頁，上市+上櫃）+ OpenAPI 最新轉讓
→ shadow 特徵。各來源失敗不中止其餘步驟，但結果明確回報 degraded。**不寫 feature_daily / signal_snapshot。**

用法：
  APP_ENV=dev python -m app.jobs.mops 2026-09-11
  APP_ENV=dev python -m app.jobs.mops 2026-09-11 --skip-holdings --skip-features
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

from app.connectors import mops as conn
from app.core.config import get_thresholds
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.importers import mops as parse
from app.importers.mops_service import import_holdings, import_transfers
from app.services.mops_features import build_mops_features

logger = get_logger("jobs.mops")
MARKETS = (parse.MARKET_TWSE, parse.MARKET_TPEX)
MODE = parse.MODE_FORWARD  # 本 job 是向前累積：寫入 ingestion_mode=forward（docs/14 §7.1）


def local_today() -> dt.date:
    """設定時區（schedule.timezone）的今天；不依賴主機本地時區。"""
    tz = get_thresholds().schedule.get("timezone", "Asia/Taipei")
    return dt.datetime.now(ZoneInfo(tz)).date()


async def _step(result: dict, label: str, coro) -> None:
    try:
        result["ok"][label] = await coro
    except Exception as e:  # noqa: BLE001 — 單一來源失敗不中止，但必須可觀測
        logger.warning("MOPS %s 失敗：%s: %s", label, type(e).__name__, e)
        result["failed"][label] = f"{type(e).__name__}: {e}"


async def _openapi_holdings(market: str) -> int:
    rows = parse.parse_openapi_holdings(await conn.fetch_openapi_holdings(market), market)
    async with get_sessionmaker()() as s:
        return await import_holdings(s, rows, mode=MODE)


async def _web_transfers(report_date: dt.date, market: str) -> int:
    html = await conn.fetch_transfer_page(report_date, market)
    batch = parse.parse_transfer_page(html, market, report_date)
    async with get_sessionmaker()() as s:
        return await import_transfers(s, batch, market, parse.SOURCE_WEB, mode=MODE)


async def _openapi_transfers(market: str) -> int:
    batch = parse.parse_openapi_transfers(await conn.fetch_openapi_transfers(market), market)
    async with get_sessionmaker()() as s:
        return await import_transfers(s, batch, market, parse.SOURCE_OPENAPI, mode=MODE)


async def _features(target: dt.date, result: dict) -> int:
    async with get_sessionmaker()() as s:
        built = await build_mops_features(s, target)
    # 歧義/不一致不中止，但必須可觀測（排程開啟前的驗收條件之一）
    if built.amendment_ambiguous:
        result["warnings"]["變更申報無法唯一匹配（轉讓特徵 NULL）"] = list(built.amendment_ambiguous)
    if built.transfer_inconsistent:
        result["warnings"]["轉讓總股數不一致（轉讓特徵 NULL）"] = list(built.transfer_inconsistent)
    return built.rows


async def run(
    target: dt.date, *, do_holdings: bool = True, do_transfers: bool = True,
    do_features: bool = True,
) -> dict:
    result: dict = {"date": str(target), "ok": {}, "failed": {}, "warnings": {}}
    if do_holdings:
        for mk in MARKETS:
            await _step(result, f"持股 OpenAPI {mk}", _openapi_holdings(mk))
    if do_transfers:
        for mk in MARKETS:
            await _step(result, f"轉讓申報網頁 {target} {mk}", _web_transfers(target, mk))
            await asyncio.sleep(conn.throttle_sec())
            await _step(result, f"轉讓申報 OpenAPI {mk}", _openapi_transfers(mk))
    if do_features:
        await _step(result, "shadow 特徵", _features(target, result))
    result["degraded"] = sorted(result["failed"])
    logger.info("MOPS %s 完成：ok=%s failed=%s warnings=%s", target, result["ok"],
                result["degraded"], result["warnings"])
    return result


async def run_scheduled(today: dt.date | None = None) -> None:
    """排程入口：處理前一日（T 日申報於 T+1 才可用）。與 EOD/回補/工作共用單飛鎖，忙碌時跳過。"""
    from app.jobs import runner

    target = (today or local_today()) - dt.timedelta(days=1)
    if not runner.try_mark("scheduled_mops", "mops", str(target)):
        logger.warning("已有工作進行中，跳過本次 MOPS %s", target)
        return
    try:
        runner._state["step"] = f"{target}：MOPS 持股/轉讓/shadow 特徵"
        runner._state["result"] = await run(target)
        runner.finish(ok=True)
    except Exception as e:  # noqa: BLE001
        logger.exception("MOPS 排程失敗 %s", target)
        runner.finish(ok=False, error=str(e))


def main() -> None:
    p = argparse.ArgumentParser(description="Phase 2 MOPS 每日匯入 + shadow 特徵（不影響正式分數）")
    p.add_argument("date", help="目標日 YYYY-MM-DD（轉讓申報抓此日；特徵以此日盤後為 as_of）")
    p.add_argument("--skip-holdings", action="store_true")
    p.add_argument("--skip-transfers", action="store_true")
    p.add_argument("--skip-features", action="store_true")
    a = p.parse_args()
    res = asyncio.run(run(
        dt.date.fromisoformat(a.date), do_holdings=not a.skip_holdings,
        do_transfers=not a.skip_transfers, do_features=not a.skip_features,
    ))
    if res["degraded"]:
        raise SystemExit(f"降級完成，失敗來源：{'、'.join(res['degraded'])}")


if __name__ == "__main__":
    main()
