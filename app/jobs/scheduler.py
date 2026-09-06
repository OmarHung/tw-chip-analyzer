"""常駐排程器:每交易日盤後自動跑 EOD 完整流程(Python 版,取代 cron + eod.sh)。

用 APScheduler AsyncIOScheduler + CronTrigger;排程時間由 config/thresholds.yaml
的 schedule 節提供(鐵則 4:排程時間必須 config 化,禁硬編碼)。job 直接 await
job 函式(daily.run / import_ticks.run),不走 subprocess。

EOD 流程(與 eod.sh 等價):
  1. daily.run(import+TAIEX+特徵,暫不落地) → 2. import_ticks(逐筆,容錯)
  → 3. daily.run(skip_import:重建含 intraday 特徵 + 落地 signal_snapshot)

用法:
  APP_ENV=dev python -m app.jobs.scheduler            # 常駐,依 config 排程
  APP_ENV=dev python -m app.jobs.scheduler --check     # 印下次執行時間後退出
  APP_ENV=dev python -m app.jobs.scheduler --now [--date YYYY-MM-DD]  # 立即跑一次
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings, get_thresholds
from app.core.logging import get_logger
from app.jobs import daily, import_ticks

logger = get_logger("jobs.scheduler")


async def run_eod(target: dt.date | None = None) -> None:
    """單次 EOD 流程;非交易日 daily 偵測無 OHLCV 會自動空跑。"""
    target = target or dt.date.today()
    logger.info("EOD 開始 %s", target)
    # 1) 行情 + 法人 + 融資 + 當週 TDCC + TAIEX + 特徵(暫不落地,待步驟 3 一次算四維)
    await daily.run(
        target, do_import=True, do_features=True, do_signals=False,
        do_tdcc=True, do_index=True,
    )
    # 2) 逐筆(Shioaji simulation);失敗/無資料不中斷整體
    try:
        await import_ticks.run(target)
    except Exception as e:  # noqa: BLE001 — 逐筆非必要,intraday 缺則中性
        logger.warning("import_ticks 失敗,intraday 將為中性:%s", e)
    # 3) 重建特徵(有逐筆則含 intraday z)+ composite 分數落地
    await daily.run(target, do_import=False, do_features=True, do_signals=True)
    logger.info("EOD 完成 %s", target)


def _build_scheduler() -> AsyncIOScheduler:
    sch = get_thresholds().schedule
    tz = sch.get("timezone", "Asia/Taipei")
    eod = sch.get("eod", {})
    scheduler = AsyncIOScheduler(timezone=tz)
    trigger = CronTrigger(
        day_of_week=eod.get("day_of_week", "mon-fri"),
        hour=eod.get("hour", 14),
        minute=eod.get("minute", 30),
        timezone=tz,
    )
    scheduler.add_job(
        run_eod, trigger, id="eod",
        misfire_grace_time=3600,  # 機器休眠錯過時的寬限
        coalesce=True,            # 多次錯過只補跑一次
        max_instances=1,
    )
    return scheduler


# --- 供 FastAPI lifespan 使用:隨 API server 起停的全域排程器 ---
_scheduler: AsyncIOScheduler | None = None


def start_scheduler() -> AsyncIOScheduler | None:
    """啟動全域排程器(FastAPI lifespan 呼叫)。

    test 環境或 config schedule.enabled=false 時不啟動;多次呼叫冪等。
    注意:多 worker 部署(uvicorn --workers>1)每個 worker 都會呼叫此函式,
    EOD 會重複跑 → 排程器所在的 API 請以「單 worker」執行。
    """
    global _scheduler
    settings = get_settings()
    enabled = get_thresholds().schedule.get("enabled", True)
    if settings.is_test or not enabled:
        logger.info(
            "排程器未啟動(env=%s, enabled=%s)", settings.app_env, enabled
        )
        return None
    if _scheduler and _scheduler.running:
        return _scheduler
    _scheduler = _build_scheduler()
    _scheduler.start()
    logger.info("排程器啟動,下次 EOD:%s", _scheduler.get_job("eod").next_run_time)
    return _scheduler


def shutdown_scheduler() -> None:
    """停止全域排程器(FastAPI lifespan 呼叫);冪等。"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("排程器已停止")
    _scheduler = None


async def _serve() -> None:
    scheduler = _build_scheduler()
    scheduler.start()
    logger.info("排程器啟動,下次 EOD:%s", scheduler.get_job("eod").next_run_time)
    while True:  # 保持 event loop 存活
        await asyncio.sleep(3600)


def main() -> None:
    p = argparse.ArgumentParser(description="EOD 常駐排程器(APScheduler)")
    p.add_argument("--now", action="store_true", help="立即跑一次 EOD 後退出")
    p.add_argument("--check", action="store_true", help="印下次執行時間後退出")
    p.add_argument("--date", help="搭配 --now 指定日期 YYYY-MM-DD")
    args = p.parse_args()

    if args.check:
        async def _check() -> None:
            scheduler = _build_scheduler()
            scheduler.start()  # next_run_time 需 start 後才計算
            job = scheduler.get_job("eod")
            print(f"下次 EOD 執行時間:{job.next_run_time}  (trigger: {job.trigger})")
            scheduler.shutdown(wait=False)

        asyncio.run(_check())
        return
    if args.now:
        target = dt.date.fromisoformat(args.date) if args.date else None
        asyncio.run(run_eod(target))
        return
    try:
        asyncio.run(_serve())
    except (KeyboardInterrupt, SystemExit):
        logger.info("排程器停止")


if __name__ == "__main__":
    main()
