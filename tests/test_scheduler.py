"""EOD 排程器測試:trigger 走 config(鐵則 4)、test env 不啟動、完整性檢查與延後重試。

重試相關測試全部以假時鐘 + 假 sleep 驅動:不打網路、不碰 DB、不真的等待。
"""
from __future__ import annotations

import datetime as dt
import types
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

import app.jobs.scheduler as scheduler
from app.core.config import get_thresholds
from app.jobs.scheduler import (
    _build_scheduler,
    _check_completeness,
    _run_eod_once,
    run_eod,
    shutdown_scheduler,
    start_scheduler,
)

TZ = ZoneInfo(get_thresholds().schedule.get("timezone", "Asia/Taipei"))


def _sources(price: int, inst: int, margin: int) -> dict:
    """組一份 daily.run() 形狀的回傳;TWSE/TPEx 各分一半以驗證「合計」而非單邊。"""
    def pair(label_a: str, label_b: str, total: int) -> dict:
        return {
            label_a: {"ok": True, "rows": total // 2, "error": None},
            label_b: {"ok": True, "rows": total - total // 2, "error": None},
        }

    sources = {
        **pair("TWSE 行情", "TPEx 行情", price),
        **pair("TWSE 法人", "TPEx 法人", inst),
        **pair("TWSE 融資券", "TPEx 融資券", margin),
    }
    return {"sources": sources, "degraded": []}


def _thresholds() -> dict:
    return get_thresholds().get("schedule", "eod", "completeness", default={}) or {}


# ---------------------------------------------------------------- trigger / 生命週期

def test_build_scheduler_reads_trigger_from_config():
    """排程時間必須來自 config/thresholds.yaml,禁硬編碼。"""
    sch = _build_scheduler()
    job = sch.get_job("eod")
    assert job is not None
    assert isinstance(job.trigger, CronTrigger)
    # config 預設 mon-fri 16:00(對齊 config/thresholds.yaml schedule.eod)
    text = str(job.trigger)
    assert "day_of_week='mon-fri'" in text
    assert "hour='16'" in text
    assert "minute='0'" in text
    sch.shutdown(wait=False) if sch.running else None


def test_scheduler_code_defaults_match_yaml():
    """YAML 若缺鍵,程式碼 fallback 不可悄悄退回舊的 14:30。"""
    from app.core.config import Thresholds

    empty = Thresholds({"schedule": {"timezone": "Asia/Taipei"}})
    sch = _build_scheduler.__globals__["get_thresholds"]
    try:
        scheduler.get_thresholds = lambda: empty
        text = str(_build_scheduler().get_job("eod").trigger)
        assert "hour='16'" in text and "minute='0'" in text
    finally:
        scheduler.get_thresholds = sch


def test_start_scheduler_disabled_in_test_env():
    """test 環境一律不啟動排程器(避免測試/CI 誤觸 EOD)。"""
    assert start_scheduler() is None


def test_shutdown_scheduler_idempotent():
    """未啟動時 shutdown 不應報錯。"""
    shutdown_scheduler()
    shutdown_scheduler()


# ---------------------------------------------------------------- 完整性檢查

def test_completeness_passes_when_all_dimensions_meet_threshold():
    """三維度都達標 → (True, None)。"""
    conf = _thresholds()
    ok, reason = _check_completeness(_sources(
        conf["min_price_rows"], conf["min_institutional_rows"], conf["min_margin_rows"],
    ))
    assert ok is True and reason is None


@pytest.mark.parametrize("dim,keyword", [
    ("price", "行情"), ("inst", "法人"), ("margin", "融資券"),
])
def test_completeness_fails_and_names_the_short_dimension(dim: str, keyword: str):
    """任一維度低於門檻 → (False, 原因),且原因要指出是哪個維度不足。"""
    conf = _thresholds()
    rows = {
        "price": conf["min_price_rows"],
        "inst": conf["min_institutional_rows"],
        "margin": conf["min_margin_rows"],
    }
    rows[dim] = 10  # 只讓這一維短缺
    ok, reason = _check_completeness(_sources(rows["price"], rows["inst"], rows["margin"]))
    assert ok is False
    assert reason is not None and keyword in reason


def test_completeness_catches_missing_tpex_market():
    """實務上最常見的不完整樣態:TPEx 整個沒進來(dev 庫實測降級日僅約 1071 筆)。

    門檻若訂得比降級日筆數還低,這個 case 會被誤判為完整——本測試釘住這件事。
    """
    only_twse = {
        "sources": {
            "TWSE 行情": {"ok": True, "rows": 1071, "error": None},
            "TPEx 行情": {"ok": False, "rows": 0, "error": "來源失敗"},
            "TWSE 法人": {"ok": True, "rows": 1042, "error": None},
            "TPEx 法人": {"ok": False, "rows": 0, "error": "來源失敗"},
            "TWSE 融資券": {"ok": True, "rows": 1036, "error": None},
            "TPEx 融資券": {"ok": False, "rows": 0, "error": "來源失敗"},
        },
        "degraded": ["TPEx 行情", "TPEx 法人", "TPEx 融資券"],
    }
    ok, reason = _check_completeness(only_twse)
    assert ok is False and reason is not None


def test_completeness_missing_source_counts_as_zero():
    """來源 label 根本不存在(整段沒跑)時視為 0 筆,不可當成達標。"""
    ok, reason = _check_completeness({"sources": {}, "degraded": []})
    assert ok is False and reason is not None


# ---------------------------------------------------------------- 忙碌 → 視同不完整

async def test_run_eod_once_returns_false_when_busy(monkeypatch):
    """單飛鎖被佔用時:不跑、不碰別人的 finish()、回 False 交給外層重試。"""
    from app.jobs import runner

    calls: list[str] = []
    monkeypatch.setattr(runner, "try_mark", lambda *a, **k: calls.append("try_mark") or False)
    monkeypatch.setattr(runner, "finish", lambda *a, **k: calls.append("finish"))

    async def _boom(*a, **k):
        raise AssertionError("忙碌時不應呼叫 daily.run")

    monkeypatch.setattr(scheduler.daily, "run", _boom)

    assert await _run_eod_once(dt.date(2026, 9, 8)) is False
    assert calls == ["try_mark"]  # 沒有 finish:釋放別人的鎖是嚴重 bug


async def test_run_eod_retries_after_busy_skip(monkeypatch):
    """需求 4:因忙碌被跳過 → 視同不完整,進同一套重試,而不是直接放棄。"""
    results = iter([False, True])
    attempts: list[dt.date] = []
    slept: list[float] = []

    async def fake_once(target: dt.date) -> bool:
        attempts.append(target)
        return next(results)

    monkeypatch.setattr(scheduler, "_run_eod_once", fake_once)
    _patch_clock(monkeypatch, dt.datetime(2026, 9, 8, 16, 0, tzinfo=TZ), slept)

    await run_eod(dt.date(2026, 9, 8))

    assert len(attempts) == 2                 # 第一次被跳過,第二次才真的跑成
    assert slept == [15 * 60]                 # 依 config interval_minutes 等待


# ---------------------------------------------------------------- 重試節流與截止時間

def _patch_clock(monkeypatch, start: dt.datetime, slept: list[float]) -> dict:
    """假時鐘:asyncio.sleep 不真的等待,只把時鐘往前推 + 記錄秒數。"""
    clock = {"now": start}

    async def fake_sleep(sec: float) -> None:
        slept.append(sec)
        clock["now"] += dt.timedelta(seconds=sec)

    # 用 shim 取代 module 內的 asyncio 名稱,避免污染全域 asyncio.sleep
    monkeypatch.setattr(scheduler, "asyncio", types.SimpleNamespace(sleep=fake_sleep))
    monkeypatch.setattr(scheduler, "_local_now", lambda: clock["now"])
    return clock


async def test_run_eod_retries_every_interval_until_deadline(monkeypatch):
    """需求 3:不完整則每 15 分鐘重試,到 18:00 截止就放棄(不會無限迴圈)。"""
    attempts: list[dt.date] = []
    slept: list[float] = []

    async def always_incomplete(target: dt.date) -> bool:
        attempts.append(target)
        return False

    monkeypatch.setattr(scheduler, "_run_eod_once", always_incomplete)
    _patch_clock(monkeypatch, dt.datetime(2026, 9, 8, 16, 0, tzinfo=TZ), slept)

    await run_eod(dt.date(2026, 9, 8))

    # 16:00 起每 15 分一次到 18:00 → 9 次嘗試、8 次等待,每次 900 秒
    assert slept == [15 * 60] * 8
    assert len(attempts) == 9


async def test_retry_sleep_never_overshoots_deadline(monkeypatch):
    """剩餘時間不足一個 interval 時,只睡到 deadline,不可睡過頭。"""
    slept: list[float] = []

    async def always_incomplete(target: dt.date) -> bool:
        return False

    monkeypatch.setattr(scheduler, "_run_eod_once", always_incomplete)
    _patch_clock(monkeypatch, dt.datetime(2026, 9, 8, 17, 50, tzinfo=TZ), slept)

    await run_eod(dt.date(2026, 9, 8))

    assert slept == [10 * 60]  # 17:50 → 18:00,而非整個 15 分鐘
    assert all(s > 0 for s in slept)


async def test_run_eod_attempts_once_even_after_deadline(monkeypatch):
    """已過截止時間才觸發(例:機器休眠後補跑):仍跑一次,但不再重試。"""
    attempts: list[dt.date] = []
    slept: list[float] = []

    async def always_incomplete(target: dt.date) -> bool:
        attempts.append(target)
        return False

    monkeypatch.setattr(scheduler, "_run_eod_once", always_incomplete)
    _patch_clock(monkeypatch, dt.datetime(2026, 9, 8, 20, 30, tzinfo=TZ), slept)

    await run_eod(dt.date(2026, 9, 8))

    assert len(attempts) == 1 and slept == []


async def test_run_eod_stops_immediately_when_complete(monkeypatch):
    """資料完整就結束,不應多睡任何一次。"""
    slept: list[float] = []

    async def complete(target: dt.date) -> bool:
        return True

    monkeypatch.setattr(scheduler, "_run_eod_once", complete)
    _patch_clock(monkeypatch, dt.datetime(2026, 9, 8, 16, 0, tzinfo=TZ), slept)

    await run_eod(dt.date(2026, 9, 8))
    assert slept == []


# ---------------------------------------------------------------- 時區(需求 5)

def test_local_now_uses_configured_timezone():
    """_local_now 走 config schedule.timezone,不依賴主機時區。"""
    tz = get_thresholds().schedule.get("timezone", "Asia/Taipei")
    now = scheduler._local_now()
    assert now.tzinfo is not None
    assert now.date() == dt.datetime.now(ZoneInfo(tz)).date()


async def test_run_eod_target_never_uses_host_date_today(monkeypatch):
    """需求 5:target 由設定時區換算,絕不呼叫主機的 date.today()。

    把 module 內的 dt.date 換成「呼叫 today() 就爆炸」的子類:只要 run_eod 還有
    任何一條路徑依賴主機時區,這個測試就會失敗。
    """
    class _ExplodingDate(dt.date):
        @classmethod
        def today(cls):  # noqa: D102
            raise AssertionError("run_eod 不可使用主機時區的 date.today()")

    monkeypatch.setattr(scheduler, "dt", types.SimpleNamespace(
        date=_ExplodingDate, datetime=dt.datetime, timedelta=dt.timedelta,
    ))

    seen: list[dt.date] = []

    async def capture(target: dt.date) -> bool:
        seen.append(target)
        return True

    monkeypatch.setattr(scheduler, "_run_eod_once", capture)

    await run_eod()  # 不給 target → 必須自己用設定時區算出「今天」

    tz = get_thresholds().schedule.get("timezone", "Asia/Taipei")
    assert seen == [dt.datetime.now(ZoneInfo(tz)).date()]
