"""每日推播「新進 BUY / AVOID」測試。

涵蓋：新進判定（逐檔比最近一筆快照）、訊息組裝/分段/跳脫、Telegram client 重試與
token 去識別、notify_signals.run 的開關狀態、EOD 排程器接線（只推今天、失敗不影響）。
全程不打網路（httpx.MockTransport / 假 client）。
"""
from __future__ import annotations

import datetime as dt
import types

import httpx
import pytest

import app.jobs.notify_signals as notify
import app.jobs.scheduler as scheduler
from app.connectors.telegram import TelegramClient, TelegramError
from app.db.models.features import SignalSnapshot
from app.db.models.settings import NotifySetting
from app.db.models.market import Stock
from app.importers.base import availability_for
from app.services.notify_settings import TelegramConfig
from app.services.signal_alerts import (
    AlertItem,
    AlertReport,
    format_telegram_messages,
    load_action_changes,
    split_messages,
)

TARGET = dt.date(2026, 9, 15)  # 週二
PREV = dt.date(2026, 9, 14)
ACTIONS = ["BUY", "AVOID"]


def _snap(symbol: str, d: dt.date, action: str, score: float, **payload) -> SignalSnapshot:
    return SignalSnapshot(
        symbol=symbol, data_date=d, available_at=availability_for(d),
        chip_score=score, action=action, reasons=["法人連買", "RR 2.5"],
        payload=payload or None,
    )


async def _seed(session, rows: list[SignalSnapshot]) -> None:
    symbols = sorted({r.symbol for r in rows})
    session.add_all([Stock(symbol=s, name=f"名{s}", market="TWSE") for s in symbols])
    await session.flush()
    session.add_all(rows)
    await session.commit()


# ---------------------------------------------------------------- 新進判定

async def test_new_entries_compare_with_latest_prior_snapshot(db_session):
    await _seed(db_session, [
        _snap("1101", PREV, "WATCH", 70), _snap("1101", TARGET, "BUY", 80),    # 新進 BUY
        _snap("2330", PREV, "BUY", 85), _snap("2330", TARGET, "BUY", 90),      # 延續
        _snap("2603", TARGET, "AVOID", 10),                                    # 無前一筆 → 新進
        _snap("2002", PREV, "AVOID", 5), _snap("2002", TARGET, "AVOID", 4),    # 延續
        _snap("3008", PREV, "BUY", 80), _snap("3008", TARGET, "WATCH", 70),    # 非推播集合
    ])
    report = await load_action_changes(db_session, TARGET, ACTIONS, lookback_days=10)

    assert report.has_snapshot and report.has_baseline
    assert [i.symbol for i in report.by_action("BUY")] == ["1101"]
    assert report.by_action("BUY")[0].prev_action == "WATCH"
    assert [i.symbol for i in report.by_action("AVOID")] == ["2603"]
    assert report.by_action("AVOID")[0].prev_action is None
    assert report.totals == {"BUY": 2, "AVOID": 2}


async def test_gap_day_does_not_make_continuing_signal_new(db_session):
    """昨日未落地（無籌碼成分）、前日 BUY、今日 BUY → 不算新進。"""
    await _seed(db_session, [
        _snap("1101", TARGET - dt.timedelta(days=4), "BUY", 80),
        _snap("1101", TARGET, "BUY", 82),
    ])
    report = await load_action_changes(db_session, TARGET, ACTIONS, lookback_days=10)
    assert report.items == ()


async def test_prior_snapshot_outside_lookback_counts_as_new(db_session):
    await _seed(db_session, [
        _snap("1101", TARGET - dt.timedelta(days=30), "BUY", 80),
        _snap("1101", TARGET, "BUY", 82),
    ])
    report = await load_action_changes(db_session, TARGET, ACTIONS, lookback_days=10)
    assert [i.symbol for i in report.items] == ["1101"]
    assert report.has_baseline is False


async def test_no_snapshot_on_target(db_session):
    await _seed(db_session, [_snap("1101", PREV, "BUY", 80)])
    report = await load_action_changes(db_session, TARGET, ACTIONS, lookback_days=10)
    assert report.has_snapshot is False


async def test_min_turnover_filters_illiquid_but_keeps_unknown(db_session):
    await _seed(db_session, [
        _snap("1101", TARGET, "BUY", 80, turnover=1_000_000.0),
        _snap("2330", TARGET, "BUY", 90, turnover=5e9),
        _snap("2603", TARGET, "BUY", 85),  # 無 turnover → 不過濾
    ])
    report = await load_action_changes(
        db_session, TARGET, ACTIONS, lookback_days=10, min_turnover=1e8
    )
    assert [i.symbol for i in report.by_action("BUY")] == ["2330", "2603"]  # 高分在前


async def test_avoid_sorted_lowest_score_first(db_session):
    await _seed(db_session, [
        _snap("1101", TARGET, "AVOID", 20), _snap("2330", TARGET, "AVOID", 3),
    ])
    report = await load_action_changes(db_session, TARGET, ACTIONS, lookback_days=10)
    assert [i.symbol for i in report.by_action("AVOID")] == ["2330", "1101"]


# ---------------------------------------------------------------- 訊息組裝

def _item(symbol: str, action: str = "BUY", **kw) -> AlertItem:
    base = dict(symbol=symbol, name=f"名{symbol}", action=action, chip_score=80.0,
                prev_action="WATCH", prev_date=PREV)
    return AlertItem(**{**base, **kw})


def _report(items: list[AlertItem], **kw) -> AlertReport:
    return AlertReport(target=TARGET, has_snapshot=True, has_baseline=True,
                       items=tuple(items), totals={"BUY": 1, "AVOID": 0}, **kw)


def test_message_contains_plan_and_escapes_html():
    item = _item("1101", name="A&B<公司>", close=50.5, change_pct=0.031,
                 entry_low=49.0, entry_high=51.0, stop_loss=46.2, tp1=56.0, tp2=61.5,
                 risk_reward=2.4, reasons=("外資 <連買>", "第二條", "第三條"))
    [msg] = format_telegram_messages(_report([item]), ACTIONS,
                                     max_items=20, max_reasons=2, max_chars=3900)
    assert "2026-09-15（二）" in msg
    assert "A&amp;B&lt;公司&gt;" in msg
    assert "（+3.1%）" in msg
    assert "進場 49–51" in msg and "停損 46.2" in msg and "TP2 61.5" in msg
    assert "外資 &lt;連買&gt;" in msg and "第三條" not in msg
    assert "🟢 <b>新進 AVOID</b>（0）" in msg


def test_max_items_truncates_with_remainder_count():
    items = [_item(f"{1000 + i}") for i in range(5)]
    [msg] = format_telegram_messages(_report(items), ACTIONS,
                                     max_items=2, max_reasons=0, max_chars=3900)
    assert "…另 3 檔未列" in msg
    assert "1002" not in msg


def test_note_and_first_run_banner():
    report = AlertReport(target=TARGET, has_snapshot=True, has_baseline=False)
    [msg] = format_telegram_messages(report, ACTIONS, max_items=5, max_reasons=1,
                                     max_chars=3900, note="資料<不完整>")
    assert "⚠️ 資料&lt;不完整&gt;" in msg
    assert "首次執行" in msg


def test_split_messages_respects_limit_without_breaking_blocks():
    blocks = ["x" * 40, "y" * 40, "z" * 40]
    msgs = split_messages(blocks, max_chars=90)
    assert msgs == ["x" * 40 + "\n" + "y" * 40, "z" * 40]
    assert all(len(m) <= 90 for m in split_messages(["w" * 250], max_chars=90))


# ---------------------------------------------------------------- Telegram client

TOKEN = "123456:SECRET-TOKEN"


def _client(handler, retries: int = 2) -> TelegramClient:
    return TelegramClient(TOKEN, "42", retries=retries, backoff_sec=0,
                          transport=httpx.MockTransport(handler))


async def test_client_sends_payload():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = req.read().decode()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    result = await _client(handler).send_message("hi")
    assert result == {"message_id": 7}
    assert seen["url"].endswith(f"/bot{TOKEN}/sendMessage")
    assert '"chat_id":"42"' in seen["body"].replace(" ", "")


async def test_client_retries_5xx_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(502, json={"ok": False, "description": "bad gateway"})
        return httpx.Response(200, json={"ok": True, "result": {}})

    await _client(handler, retries=2).send_message("hi")
    assert len(calls) == 3


async def test_client_does_not_retry_4xx():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(400, json={"ok": False, "description": "chat not found"})

    with pytest.raises(TelegramError, match="chat not found"):
        await _client(handler).send_message("hi")
    assert len(calls) == 1


async def test_client_error_never_leaks_token():
    def handler(req):
        raise httpx.ConnectError(f"cannot connect to {req.url}")

    with pytest.raises(TelegramError) as exc:
        await _client(handler, retries=1).send_message("hi")
    assert "SECRET-TOKEN" not in str(exc.value)
    assert exc.value.__cause__ is None


async def test_httpx_request_log_redacts_token(caplog):
    def handler(req):
        return httpx.Response(200, json={"ok": True, "result": {}})

    with caplog.at_level("INFO", logger="httpx"):
        await _client(handler).send_message("hi")
    logged = "\n".join(r.getMessage() for r in caplog.records if r.name == "httpx")
    assert "/bot***/sendMessage" in logged
    assert "SECRET-TOKEN" not in logged


async def test_client_rejects_overlong_message():
    with pytest.raises(ValueError):
        await _client(lambda r: httpx.Response(200)).send_message("x" * 5000)


# ---------------------------------------------------------------- notify_signals.run

async def _set_conf(session, **fields) -> None:
    """寫入 UI 設定列（DB 優先於 .env / YAML）。"""
    base = {"enabled": True, "bot_token": "123456:" + "x" * 35, "chat_id": "42"}
    session.add(NotifySetting(channel="telegram", **{**base, **fields}))
    await session.commit()


async def test_run_disabled_and_unconfigured(db_session):
    await _set_conf(db_session, enabled=False)
    assert (await notify.run(TARGET))["status"] == "disabled"
    row = await db_session.get(NotifySetting, "telegram")
    row.enabled, row.bot_token = True, ""
    await db_session.commit()
    assert (await notify.run(TARGET))["status"] == "unconfigured"


async def test_run_no_snapshot(db_session):
    await _set_conf(db_session)
    assert (await notify.run(TARGET))["status"] == "no_snapshot"


async def test_run_sends_and_reports_error(db_session, monkeypatch):
    await _seed(db_session, [_snap("1101", TARGET, "BUY", 80)])
    await _set_conf(db_session, actions=ACTIONS)
    sent: list[str] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def send_message(self, text):
            sent.append(text)
            return {}

    monkeypatch.setattr(TelegramConfig, "client", lambda self: FakeClient())
    result = await notify.run(TARGET, note="測試")
    assert result["status"] == "sent" and result["new"] == {"BUY": 1, "AVOID": 0}
    assert "1101" in sent[0] and "⚠️ 測試" in sent[0]

    class BrokenClient(FakeClient):
        async def send_message(self, text):
            raise TelegramError("HTTP 401: Unauthorized")

    monkeypatch.setattr(TelegramConfig, "client", lambda self: BrokenClient())
    result = await notify.run(TARGET)
    assert result["status"] == "error" and result["sent"] == 0


async def test_run_dry_run_works_without_token(db_session, capsys):
    await _seed(db_session, [_snap("2603", TARGET, "AVOID", 5)])
    await _set_conf(db_session, enabled=False, bot_token="", chat_id="", actions=ACTIONS)
    result = await notify.run(TARGET, dry_run=True)
    assert result["status"] == "dry_run" and result["configured"] is False
    assert "2603" in capsys.readouterr().out


# ---------------------------------------------------------------- 排程器接線

def _patch_scheduler(monkeypatch, *, today: dt.date, is_test: bool = False) -> list:
    calls: list = []

    async def fake_run(target, note=None):
        calls.append((target, note))
        return {"status": "sent"}

    monkeypatch.setattr(notify, "run", fake_run)
    monkeypatch.setattr(scheduler, "get_settings",
                        lambda: types.SimpleNamespace(is_test=is_test))
    monkeypatch.setattr(scheduler, "_local_now",
                        lambda: dt.datetime.combine(today, dt.time(16, 30), scheduler.ZoneInfo("Asia/Taipei")))
    return calls


async def test_eod_complete_triggers_notify_for_today(monkeypatch):
    calls = _patch_scheduler(monkeypatch, today=TARGET)

    async def complete(target):
        return True

    monkeypatch.setattr(scheduler, "_run_eod_once", complete)
    await scheduler.run_eod(TARGET)
    assert calls == [(TARGET, None)]


async def test_eod_incomplete_at_deadline_notifies_with_note(monkeypatch):
    calls = _patch_scheduler(monkeypatch, today=TARGET)
    monkeypatch.setattr(scheduler, "_local_now", lambda: dt.datetime.combine(
        TARGET, dt.time(19, 0), scheduler.ZoneInfo("Asia/Taipei")))
    monkeypatch.setattr(scheduler, "_last_incomplete_reason", lambda t: "法人 0 筆 < 門檻 900")

    async def incomplete(target):
        return False

    monkeypatch.setattr(scheduler, "_run_eod_once", incomplete)
    await scheduler.run_eod(TARGET)
    assert len(calls) == 1 and "法人 0 筆" in calls[0][1]


async def test_notify_skips_past_dates_and_test_env(monkeypatch):
    calls = _patch_scheduler(monkeypatch, today=TARGET)
    await scheduler._notify_signals(PREV)
    assert calls == []
    calls = _patch_scheduler(monkeypatch, today=TARGET, is_test=True)
    await scheduler._notify_signals(TARGET)
    assert calls == []


async def test_notify_failure_never_raises(monkeypatch):
    _patch_scheduler(monkeypatch, today=TARGET)

    async def boom(target, note=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(notify, "run", boom)
    await scheduler._notify_signals(TARGET)  # 不拋
