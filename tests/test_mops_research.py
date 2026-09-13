"""scripts/mops_factor_oos 的統計純函式（docs/14 §7）與 MOPS 排程設定。"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from apscheduler.triggers.cron import CronTrigger

from app.jobs import tasks
from app.jobs.scheduler import _build_scheduler
from scripts.mops_factor_oos import day_ic, ic_stats, residualize, split_days


def test_split_embargo_gap_covers_max_horizon():
    days = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(100)]
    train, test = split_days(days, embargo=20)
    assert len(train) == 50 and test[0] == days[70]
    assert days.index(test[0]) - days.index(train[-1]) > 20


def test_day_ic_requires_min_names_and_variation():
    v = pd.Series(range(10), dtype=float)
    assert day_ic(v, v * 2, min_names=20) is None
    assert day_ic(v, v * 2, min_names=5) == pytest.approx(1.0)
    assert day_ic(pd.Series([1.0] * 10), v, min_names=5) is None


def test_ic_stats_reports_naive_and_newey_west():
    rng = np.random.default_rng(0)
    ics = list(0.02 + 0.05 * rng.standard_normal(60))
    s = ic_stats(ics, horizon=20)
    assert s["n_days"] == 60 and s["t_naive"] is not None and s["t_nw"] is not None
    assert ic_stats([0.1], horizon=5)["ic"] is None


def test_residualize_removes_baseline_rank():
    b = pd.Series(np.arange(30, dtype=float))
    resid = residualize(b * 3 + 1, b)  # 完全由 baseline 決定 → 殘差 0
    assert np.allclose(resid.dropna(), 0.0)
    assert residualize(b, pd.Series([np.nan] * 30)).isna().all()


def test_mops_job_scheduled_from_config(monkeypatch):
    import app.jobs.scheduler as scheduler_mod
    from app.core.config import Thresholds, load_yaml_thresholds

    raw = load_yaml_thresholds()
    raw["mops"]["schedule"]["enabled"] = True  # 明確開啟才排入（關閉情境見 test_mops_review_fixes）
    monkeypatch.setattr(scheduler_mod, "get_thresholds", lambda: Thresholds(raw))
    sch = _build_scheduler()
    job = sch.get_job("mops")
    assert job is not None and isinstance(job.trigger, CronTrigger)
    text = str(job.trigger)
    assert "day_of_week='mon-sat'" in text and "hour='8'" in text and "minute='10'" in text
    assert sch.get_job("eod") is not None  # EOD 不受影響


def test_mops_tasks_whitelisted_with_validated_params():
    spec = tasks.TASKS["backfill_mops_transfers"]
    assert tasks.build_argv(spec, {"start": "2026-09-01", "dry_run": True}) == [
        "--start", "2026-09-01", "--dry-run"]
    assert tasks.TASKS["rebuild_mops_features"].rebuilds_scores is False
    assert tasks.TASKS["mops_factor_oos"].category == "research"
