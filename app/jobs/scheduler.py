"""常駐排程器:每交易日盤後自動跑 EOD 完整流程(Python 版,取代 cron + eod.sh)。

用 APScheduler AsyncIOScheduler + CronTrigger;排程時間由 config/thresholds.yaml
的 schedule 節提供(鐵則 4:排程時間必須 config 化,禁硬編碼)。job 直接 await
job 函式(daily.run / import_ticks.run),不走 subprocess。

每日兩段(兩段各自有完整性檢查與延後重試,門檻/時間讀 schedule.eod / schedule.credit):

EOD(16:00,與 eod.sh 等價):
  1. daily.run(import+TAIEX+特徵,暫不落地) → 2. import_ticks(逐筆,容錯)
  → 3. daily.run(skip_import:重建含 intraday 特徵 + 落地 signal_snapshot)
  完整性只看行情/法人(TPEx 報表偶爾 5xx,16:20 前後補齊)。

信用補抓(晚間,schedule.credit):融資券(TWSE+TPEx)與借券交易所晚間才公布(線上實測
首次入庫皆在 20:53~23:20,09-15 當日 19:37 仍查無資料),16:00 的 EOD 從來抓不到。
  1. daily.run_credit(只抓三張信用報表) → 到齊(或有新列)才 2. 重建特徵 + 落地
  → 3. notify_signals(推播新進 BUY / AVOID;失敗不影響排程)
推播放在這段:資料完整後才發,避免推出缺融資券/借券成分、晚間又會變的名單。

不完整、或因單飛鎖被回補/腳本佔用而根本沒跑成,都會每隔 retry.interval_minutes
重試一次,直到 retry.deadline_hour:deadline_minute 為止。

用法:
  APP_ENV=dev python -m app.jobs.scheduler            # 常駐,依 config 排程
  APP_ENV=dev python -m app.jobs.scheduler --check     # 印下次執行時間後退出
  APP_ENV=dev python -m app.jobs.scheduler --now [--date YYYY-MM-DD]  # 立即跑一次 EOD
  APP_ENV=dev python -m app.jobs.scheduler --credit [--date YYYY-MM-DD]  # 立即跑信用補抓
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import get_settings, get_thresholds
from app.core.logging import get_logger
from app.jobs import daily, import_ticks

logger = get_logger("jobs.scheduler")


def _local_now() -> dt.datetime:
    """config schedule.timezone 的當下時間(tz-aware)。

    刻意不用 dt.date.today() / dt.datetime.now():主機時區不保證是台北,
    在 UTC 機器上跑會在台北時間 08:00 前把「今天」算成昨天。
    """
    tz = get_thresholds().schedule.get("timezone", "Asia/Taipei")
    return dt.datetime.now(ZoneInfo(tz))


# 完整性檢查維度(依排程段):(顯示名, config 鍵, 來源 label)。
# label 必須與 daily.SourceReport 寫入的字串一致,多個 label(TWSE + TPEx)合計後比門檻。
_Dimension = tuple[str, str, tuple[str, ...]]
_COMPLETENESS_DIMENSIONS: dict[str, tuple[_Dimension, ...]] = {
    "eod": (
        ("行情", "min_price_rows", ("TWSE 行情", "TPEx 行情")),
        ("法人", "min_institutional_rows", ("TWSE 法人", "TPEx 法人")),
    ),
    "credit": (
        ("融資券", "min_margin_rows", ("TWSE 融資券", "TPEx 融資券")),
        # 借券未公布時 TWT93U 回 stat=OK 但 0 筆、不拋錯,只有筆數檢查抓得到
        ("借券", "min_sbl_rows", ("SBL 借券",)),
    ),
}


def _check_completeness(import_res: dict, section: str = "eod") -> tuple[bool, str | None]:
    """該段各維度是否都達到 config 的最低筆數;回 (是否完整, 不完整原因)。

    門檻一律讀 config schedule.<section>.completeness(鐵則 4)。某維度缺鍵時該維度門檻
    視為 0(= 不檢查),而非套一個硬編碼預設——否則 config 少一個鍵就會讓每天排程
    都被判成不完整,白白重試到截止時間。
    """
    conf = get_thresholds().get("schedule", section, "completeness", default={}) or {}
    sources = import_res.get("sources", {}) or {}
    reasons: list[str] = []
    for name, key, labels in _COMPLETENESS_DIMENSIONS[section]:
        threshold = conf.get(key, 0)
        rows = sum((sources.get(label) or {}).get("rows", 0) or 0 for label in labels)
        if rows < threshold:
            reasons.append(f"{name} {rows} 筆 < 門檻 {threshold}")
    if reasons:
        return False, ";".join(reasons)
    return True, None


async def _run_eod_once(target: dt.date) -> bool:
    """跑一次 EOD 流程;回傳「本次是否成功且資料完整」。

    非交易日 daily 偵測無 OHLCV 會自動空跑。與手動回補共用忙碌旗標(runner)。
    回 False 的三種情形都交由 run_eod 安排重試:
      1. 忙碌旗標被其他回補/腳本佔用 → 這次根本沒跑到
      2. 流程中途拋例外
      3. 跑完了但核心資料未達完整性門檻(TPEx 報表偶爾 5xx、法人比行情晚上線)
    """
    from app.jobs import runner

    if not runner.try_mark("scheduled_eod", "eod", str(target)):
        # 注意:沒搶到旗標就不能碰 runner.finish(),那是別人的鎖。
        logger.warning("已有回補/工作進行中,本次 EOD %s 未執行,稍後重試", target)
        return False
    logger.info("EOD 開始 %s", target)
    try:
        # 1) 行情 + 法人 + 當週 TDCC + TAIEX + 特徵(暫不落地,待步驟 3 一次算四維)
        runner._state["step"] = f"{target}:匯入 + 特徵"
        import_res = await daily.run(
            target, do_import=True, do_features=True, do_signals=False,
            do_tdcc=True, do_index=True,
        )
        # 2) 逐筆(Shioaji simulation);失敗/無資料不中斷整體
        runner._state["step"] = f"{target}:逐筆匯入"
        try:
            tick_res = await import_ticks.run(
                target, on_progress=lambda p: runner._state.update(progress=p)
            )
        except Exception as e:  # noqa: BLE001 — 逐筆非必要,intraday 缺則排除該成分
            logger.warning("import_ticks 失敗,intraday 將排除:%s", e)
            tick_res = {"error": str(e)}
        # 3) 重建特徵(有逐筆則含 intraday z)+ composite 分數落地
        runner._state["step"] = f"{target}:重建特徵 + 落地"
        runner._state["progress"] = None
        await daily.run(target, do_import=False, do_features=True, do_signals=True)
        complete, reason = _check_completeness(import_res)
        runner._state["result"] = {
            "date": str(target), "mode": "scheduled_eod",
            "degraded": import_res["degraded"], "sources": import_res["sources"],
            "ticks": tick_res,
            "complete": complete, "incomplete_reason": reason,
        }
        # 不完整仍算「這次跑完了」(資料有進、分數有落地),只是還要再補一輪,
        # 故 finish(ok=True):狀態頁不該把「等報表上線」顯示成錯誤。
        runner.finish(ok=True)
        if complete:
            logger.info("EOD 完成 %s", target)
        else:
            logger.warning("EOD %s 資料不完整:%s", target, reason)
        return complete
    except Exception as e:  # noqa: BLE001
        logger.exception("EOD 失敗 %s", target)
        runner.finish(ok=False, error=str(e))
        return False


async def _run_credit_once(target: dt.date) -> bool:
    """跑一次信用補抓;回傳「融資券 + 借券是否到齊」。

    有新列才重建特徵 + 落地(未公布的嘗試只是三個 GET,不重算);部分到齊也重建,
    讓截止時仍不完整的日子至少用上已公布的部分。回 False 的情形同 _run_eod_once。
    """
    from app.jobs import runner

    if not runner.try_mark("scheduled_credit", "credit", str(target)):
        logger.warning("已有回補/工作進行中,本次信用補抓 %s 未執行,稍後重試", target)
        return False
    logger.info("信用補抓開始 %s", target)
    try:
        runner._state["step"] = f"{target}:融資券 + 借券"
        import_res = await daily.run_credit(target)
        complete, reason = _check_completeness(import_res, "credit")
        rows = sum((v or {}).get("rows", 0) or 0 for v in import_res["sources"].values())
        if rows > 0:
            runner._state["step"] = f"{target}:重建特徵 + 落地"
            await daily.run(target, do_import=False, do_features=True, do_signals=True)
        runner._state["result"] = {
            "date": str(target), "mode": "scheduled_credit",
            "degraded": import_res["degraded"], "sources": import_res["sources"],
            "complete": complete, "incomplete_reason": reason,
        }
        runner.finish(ok=True)
        if complete:
            logger.info("信用補抓完成 %s", target)
        else:
            logger.warning("信用補抓 %s 資料不完整:%s", target, reason)
        return complete
    except Exception as e:  # noqa: BLE001
        logger.exception("信用補抓失敗 %s", target)
        runner.finish(ok=False, error=str(e))
        return False


def _last_incomplete_reason(target: dt.date) -> str | None:
    from app.jobs import runner

    result = runner._state.get("result") or {}
    if result.get("date") != str(target):
        return None
    return result.get("incomplete_reason")


# EOD 到截止仍不完整的原因(date → reason),留給同日晚間推播附註;行程重啟即遺失(僅影響附註)。
_eod_incomplete: dict[dt.date, str] = {}


async def _notify_signals(target: dt.date, note: str | None = None) -> None:
    """信用補抓後推播新進 BUY / AVOID(notify.telegram)。失敗只記 log,絕不影響排程。

    只推「今天」:`--now --date` 補算舊日期時不該對 Telegram 補發過期名單。
    test 環境一律不送(.env 可能含真實 token)。
    """
    if get_settings().is_test or target != _local_now().date():
        return
    from app.jobs import notify_signals

    try:
        result = await notify_signals.run(target, note=note)
        logger.info("推播 %s:%s", target, result)
    except Exception:  # noqa: BLE001 — 推播是附加功能
        logger.exception("推播 %s 失敗", target)


# 各段 retry 截止時間的程式碼預設(須與 config/thresholds.yaml 一致)
_DEADLINE_DEFAULTS = {"eod": (18, 0), "credit": (23, 30)}


async def _retry_until_deadline(
    section: str, label: str, target: dt.date, once
) -> bool:
    """跑 once(target),不完整(或被忙碌擋掉)就每隔 N 分鐘重試至截止時間;回是否完成。

    截止時間取「當下時區的今天」的 deadline_hour:deadline_minute,與 target 無關
    ——它描述的是今天這個重試視窗的牆鐘邊界,所以手動補算舊日期(--now --date)
    在截止時間後執行時只會跑一次就結束,不會空轉。
    """
    retry = get_thresholds().get("schedule", section, "retry", default={}) or {}
    interval_sec = retry.get("interval_minutes", 15) * 60
    d_hour, d_minute = _DEADLINE_DEFAULTS[section]
    deadline = _local_now().replace(
        hour=retry.get("deadline_hour", d_hour), minute=retry.get("deadline_minute", d_minute),
        second=0, microsecond=0,
    )
    attempt = 0
    while True:
        attempt += 1
        # 先跑再看截止時間:確保任何情況下至少嘗試一次(即使已過 deadline)。
        if await once(target):
            return True
        remaining = (deadline - _local_now()).total_seconds()
        if remaining <= 0:
            logger.error(
                "%s %s 已達截止時間 %s 仍不完整,放棄重試(共嘗試 %d 次)",
                label, target, deadline.strftime("%H:%M"), attempt,
            )
            return False
        # 不可睡過頭跨越 deadline,否則最後一次重試會落在截止時間之外。
        sleep_sec = min(interval_sec, remaining)
        logger.warning(
            "%s %s 第 %d 次嘗試未完成,%.0f 分鐘後重試(截止 %s)",
            label, target, attempt, sleep_sec / 60, deadline.strftime("%H:%M"),
        )
        await asyncio.sleep(sleep_sec)


async def run_eod(target: dt.date | None = None) -> None:
    """EOD 進入點(不推播;推播在晚間信用補抓後)。"""
    target = target or _local_now().date()   # 需求 5:用 config 時區,不用主機 date.today()
    if await _retry_until_deadline("eod", "EOD", target, _run_eod_once):
        _eod_incomplete.pop(target, None)
    else:
        _eod_incomplete[target] = _last_incomplete_reason(target) or "原因未知"


async def run_credit(target: dt.date | None = None) -> None:
    """晚間信用補抓進入點:補融資券 + 借券 → 重建特徵 → 推播。

    當日 EOD 沒落地任何行情(非交易日,或 EOD 整段失敗)就不抓也不推——融資券端點
    在假日同樣回 0 筆,否則會空轉到截止時間。
    """
    target = target or _local_now().date()
    if not await _has_daily_price(target):
        logger.info("信用補抓 %s 略過:當日無行情(非交易日或 EOD 未成功)", target)
        return
    notes: list[str] = []
    if target in _eod_incomplete:
        notes.append(f"EOD 至截止時間仍不完整({_eod_incomplete[target]})")
    if not await _retry_until_deadline("credit", "信用補抓", target, _run_credit_once):
        reason = _last_incomplete_reason(target)
        notes.append(f"融資券/借券至截止時間仍不完整({reason or '原因未知'})")
    # 分數已落地(不完整只是缺部分來源)仍推播,但標註;全無快照時 notify 自行略過
    note = ";".join(notes) + ",名單可能失真" if notes else None
    await _notify_signals(target, note=note)


async def _has_daily_price(target: dt.date) -> bool:
    from sqlalchemy import exists, select

    from app.db.models.market import DailyPrice
    from app.db.session import get_sessionmaker

    async with get_sessionmaker()() as s:
        return bool(await s.scalar(select(exists().where(DailyPrice.data_date == target))))


def _build_scheduler() -> AsyncIOScheduler:
    sch = get_thresholds().schedule
    tz = sch.get("timezone", "Asia/Taipei")
    eod = sch.get("eod", {})
    scheduler = AsyncIOScheduler(timezone=tz)
    trigger = CronTrigger(
        day_of_week=eod.get("day_of_week", "mon-fri"),
        hour=eod.get("hour", 16),      # 預設須與 config/thresholds.yaml 一致,
        minute=eod.get("minute", 0),   # 否則 YAML 缺鍵時會悄悄退回舊的 14:30
        timezone=tz,
    )
    scheduler.add_job(
        run_eod, trigger, id="eod",
        misfire_grace_time=3600,  # 機器休眠錯過時的寬限
        coalesce=True,            # 多次錯過只補跑一次
        max_instances=1,
    )
    credit = sch.get("credit", {})
    scheduler.add_job(
        run_credit,
        CronTrigger(
            day_of_week=credit.get("day_of_week", "mon-fri"),
            hour=credit.get("hour", 20),      # 預設須與 config/thresholds.yaml 一致
            minute=credit.get("minute", 0),
            timezone=tz,
        ),
        id="credit", misfire_grace_time=3600, coalesce=True, max_instances=1,
    )
    # Phase 2 MOPS(shadow-only,docs/14):時間讀 config mops.schedule,與 EOD 共用單飛鎖
    mops = get_thresholds().get("mops", "schedule", default={}) or {}
    if mops.get("enabled", False):
        from app.jobs.mops import run_scheduled as run_mops

        scheduler.add_job(
            run_mops,
            CronTrigger(
                day_of_week=mops.get("day_of_week", "mon-sat"),
                hour=mops.get("hour", 8),
                minute=mops.get("minute", 10),
                timezone=tz,
            ),
            id="mops", misfire_grace_time=3600, coalesce=True, max_instances=1,
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


def scheduler_status() -> dict:
    """回傳排程現況(供 /api/ops/status 顯示):enabled、時區、EOD 觸發、下次執行。

    排程時間一律讀 config(鐵則 4);next_run 需排程器已 start 才算得出。
    """
    sch = get_thresholds().schedule
    eod = sch.get("eod", {})
    running = bool(_scheduler and _scheduler.running)
    next_run = None
    if running:
        job = _scheduler.get_job("eod")
        if job is not None and job.next_run_time is not None:
            next_run = job.next_run_time.isoformat()
    return {
        "enabled": sch.get("enabled", True),
        "running": running,
        "timezone": sch.get("timezone", "Asia/Taipei"),
        "eod": {
            "day_of_week": eod.get("day_of_week", "mon-fri"),
            "hour": eod.get("hour", 16),
            "minute": eod.get("minute", 0),
        },
        "next_run": next_run,
    }


async def _serve() -> None:
    scheduler = _build_scheduler()
    scheduler.start()
    logger.info("排程器啟動,下次 EOD:%s", scheduler.get_job("eod").next_run_time)
    while True:  # 保持 event loop 存活
        await asyncio.sleep(3600)


def main() -> None:
    p = argparse.ArgumentParser(description="EOD 常駐排程器(APScheduler)")
    p.add_argument("--now", action="store_true", help="立即跑一次 EOD 後退出")
    p.add_argument("--credit", action="store_true", help="立即跑一次信用補抓(含推播)後退出")
    p.add_argument("--check", action="store_true", help="印下次執行時間後退出")
    p.add_argument("--date", help="搭配 --now 指定日期 YYYY-MM-DD")
    args = p.parse_args()

    if args.check:
        async def _check() -> None:
            scheduler = _build_scheduler()
            scheduler.start()  # next_run_time 需 start 後才計算
            for job_id, name in (("eod", "EOD"), ("credit", "信用補抓")):
                job = scheduler.get_job(job_id)
                print(f"下次 {name} 執行時間:{job.next_run_time}  (trigger: {job.trigger})")
            scheduler.shutdown(wait=False)

        asyncio.run(_check())
        return
    if args.now or args.credit:
        target = dt.date.fromisoformat(args.date) if args.date else None
        asyncio.run(run_credit(target) if args.credit else run_eod(target))
        return
    try:
        asyncio.run(_serve())
    except (KeyboardInterrupt, SystemExit):
        logger.info("排程器停止")


if __name__ == "__main__":
    main()
