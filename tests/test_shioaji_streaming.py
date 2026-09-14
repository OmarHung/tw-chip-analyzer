"""Callback 訂閱骨架（spike，docs/16 §A1）的單元測試——不連真實 Shioaji，只驗證

訂閱管理邏輯本身：參數驗證、訂閱/退訂呼叫的分派、callback 轉換與分派邏輯。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.connectors import shioaji_market as sm


class _FakeContract:
    def __init__(self, code: str):
        self.code = code


class _FakeContracts:
    def __init__(self, known: set[str]):
        self._known = known

    class _Stocks:
        def __init__(self, outer: "_FakeContracts"):
            self._outer = outer

        def get(self, symbol: str):
            if symbol in self._outer._known:
                return _FakeContract(symbol)
            return None

    @property
    def Stocks(self):  # noqa: N802 — 對齊 Shioaji SDK 命名
        return _FakeContracts._Stocks(self)


class _FakeApi:
    def __init__(self, known: set[str]):
        self.Contracts = _FakeContracts(known)
        self.subscribed: list[tuple[str, object]] = []
        self.unsubscribed: list[tuple[str, object]] = []
        self.registered_callback = None

    def set_on_tick_stk_v1_callback(self, cb):
        self.registered_callback = cb

    def subscribe(self, contract, quote_type=None):
        self.subscribed.append((contract.code, quote_type))

    def unsubscribe(self, contract, quote_type=None):
        self.unsubscribed.append((contract.code, quote_type))


class _FakeTick:
    def __init__(self, code: str, close: float, volume: int, tick_type: int):
        self.code = code
        self.close = close
        self.volume = volume
        self.tick_type = tick_type
        self.datetime = dt.datetime(2026, 9, 14, 9, 30, 0)


@pytest.fixture(autouse=True)
def _reset_subscription_state(monkeypatch):
    """每個 test 重置模組級訂閱狀態，避免互相污染（狀態本來就是 process 內單例）。"""
    monkeypatch.setattr(sm, "_tick_callbacks", {})
    monkeypatch.setattr(sm, "_callback_registered", False)
    yield


def test_subscribe_ticks_rejects_more_than_max_symbols(monkeypatch):
    fake = _FakeApi(known={"2330", "2317", "2454", "2882"})
    monkeypatch.setattr(sm, "_get_api", lambda: fake)

    with pytest.raises(ValueError, match="超過 spike 節流上限"):
        sm.subscribe_ticks(["2330", "2317", "2454", "2882"], on_tick=lambda t: None)

    assert fake.subscribed == [], "超過上限時不應對任何 symbol 呼叫 SDK subscribe"


def test_subscribe_ticks_registers_callback_once_and_calls_sdk_subscribe(monkeypatch):
    fake = _FakeApi(known={"2330", "2317"})
    monkeypatch.setattr(sm, "_get_api", lambda: fake)

    subscribed = sm.subscribe_ticks(["2330", "2317"], on_tick=lambda t: None)

    assert subscribed == ["2330", "2317"]
    assert [c for c, _ in fake.subscribed] == ["2330", "2317"]
    assert fake.registered_callback is sm._dispatch_tick
    assert sm.subscribed_symbols() == ["2317", "2330"]


def test_subscribe_ticks_skips_unknown_symbol(monkeypatch):
    fake = _FakeApi(known={"2330"})
    monkeypatch.setattr(sm, "_get_api", lambda: fake)

    subscribed = sm.subscribe_ticks(["2330", "9999"], on_tick=lambda t: None)

    assert subscribed == ["2330"]
    assert sm.subscribed_symbols() == ["2330"]


def test_unsubscribe_ticks_removes_from_registry(monkeypatch):
    fake = _FakeApi(known={"2330"})
    monkeypatch.setattr(sm, "_get_api", lambda: fake)

    sm.subscribe_ticks(["2330"], on_tick=lambda t: None)
    assert sm.subscribed_symbols() == ["2330"]

    sm.unsubscribe_ticks(["2330"])

    assert sm.subscribed_symbols() == []
    assert [c for c, _ in fake.unsubscribed] == ["2330"]


def test_unsubscribe_ticks_is_noop_for_unknown_symbol(monkeypatch):
    fake = _FakeApi(known=set())
    monkeypatch.setattr(sm, "_get_api", lambda: fake)

    sm.unsubscribe_ticks(["9999"])  # 不應丟例外

    assert fake.unsubscribed == []


def test_dispatch_tick_calls_matching_symbol_callback_with_converted_dict(monkeypatch):
    fake = _FakeApi(known={"2330"})
    monkeypatch.setattr(sm, "_get_api", lambda: fake)

    received: list[dict] = []
    sm.subscribe_ticks(["2330"], on_tick=received.append)

    tick = _FakeTick(code="2330", close=650.0, volume=3, tick_type=1)
    sm._dispatch_tick(tick)

    assert len(received) == 1
    assert received[0]["code"] == "2330"
    assert received[0]["price"] == 650.0
    assert received[0]["volume"] == 3
    assert received[0]["side"] == 1  # tick_type=1 → 買/外盤
    assert received[0]["bid"] is None and received[0]["ask"] is None


def test_dispatch_tick_ignores_unsubscribed_symbol(monkeypatch):
    fake = _FakeApi(known={"2330"})
    monkeypatch.setattr(sm, "_get_api", lambda: fake)

    received: list[dict] = []
    sm.subscribe_ticks(["2330"], on_tick=received.append)

    other = _FakeTick(code="2317", close=100.0, volume=1, tick_type=0)
    sm._dispatch_tick(other)  # 不應觸發 2330 的 callback，也不應丟例外

    assert received == []


def test_dispatch_tick_callback_exception_does_not_propagate(monkeypatch):
    fake = _FakeApi(known={"2330"})
    monkeypatch.setattr(sm, "_get_api", lambda: fake)

    def boom(_tick: dict) -> None:
        raise RuntimeError("boom")

    sm.subscribe_ticks(["2330"], on_tick=boom)

    tick = _FakeTick(code="2330", close=1.0, volume=1, tick_type=0)
    sm._dispatch_tick(tick)  # 不應向外拋出例外（鐵則 11：callback 出錯不可打斷派送）
