"""EOD 排程器測試:trigger 走 config(鐵則 4)、test env 不啟動。"""
from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger

from app.jobs.scheduler import (
    _build_scheduler,
    shutdown_scheduler,
    start_scheduler,
)


def test_build_scheduler_reads_trigger_from_config():
    """排程時間必須來自 config/thresholds.yaml,禁硬編碼。"""
    sch = _build_scheduler()
    job = sch.get_job("eod")
    assert job is not None
    assert isinstance(job.trigger, CronTrigger)
    # config 預設 mon-fri 14:30(對齊 config/thresholds.yaml schedule.eod)
    text = str(job.trigger)
    assert "day_of_week='mon-fri'" in text
    assert "hour='14'" in text
    assert "minute='30'" in text
    sch.shutdown(wait=False) if sch.running else None


def test_start_scheduler_disabled_in_test_env():
    """test 環境一律不啟動排程器(避免測試/CI 誤觸 EOD)。"""
    assert start_scheduler() is None


def test_shutdown_scheduler_idempotent():
    """未啟動時 shutdown 不應報錯。"""
    shutdown_scheduler()
    shutdown_scheduler()
