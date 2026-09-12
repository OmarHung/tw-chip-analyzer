"""docs/09 第 2 批：EOD 非核心來源 fail-soft、各來源狀態可觀測（BUG-07/08）。

所有 connector / importer 以假函式替換，不打網路、不碰 DB。
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.jobs import daily

TARGET = dt.date(2026, 9, 8)


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def _aboom(*a, **k):
    raise RuntimeError("來源暫時失敗")


@pytest.fixture
def fake_daily(monkeypatch):
    """把 daily 內所有 fetch/import 換成假函式，回傳呼叫紀錄。"""
    calls: list[str] = []

    def fetcher(name):
        async def f(*a, **k):
            calls.append(f"fetch:{name}")
            return [name]
        return f

    def importer(name, n=10):
        async def f(*a, **k):
            calls.append(f"import:{name}")
            return (n, n) if name == "tdcc" else n
        return f

    monkeypatch.setattr(daily, "get_sessionmaker", lambda: _FakeSession)
    for mod, names in (
        (daily.twse_conn, [
            "fetch_index_month", "fetch_ohlcv", "fetch_institutional", "fetch_margin",
            "fetch_sbl", "fetch_ex_dividend", "fetch_par_change",
            "fetch_capital_reduction_forecast", "fetch_capital_reduction",
            "fetch_ex_rights_forecast", "fetch_company_profiles",
        ]),
        (daily.tpex_conn, [
            "fetch_ohlcv", "fetch_institutional", "fetch_margin", "fetch_ex_dividend",
            "fetch_par_change", "fetch_capital_reduction", "fetch_company_profiles",
        ]),
        (daily.tdcc_conn, ["fetch_shareholder_distribution"]),
    ):
        prefix = mod.__name__.rsplit(".", 1)[-1]
        for n in names:
            monkeypatch.setattr(mod, n, fetcher(f"{prefix}.{n}"))
    for n in [x for x in dir(daily) if x.startswith("import_")]:
        monkeypatch.setattr(daily, n, importer(n.removeprefix("import_")))
    return calls


async def _run():
    return await daily.run(
        TARGET, do_features=False, do_signals=False, do_tdcc=True, do_index=True
    )


async def test_all_sources_ok_reports_status(fake_daily):
    res = await _run()
    assert res["degraded"] == []
    assert res["sources"]["TAIEX"]["ok"] and res["sources"]["TDCC"]["ok"]


async def test_taiex_failure_does_not_abort_core_import(fake_daily, monkeypatch):
    monkeypatch.setattr(daily.twse_conn, "fetch_index_month", _aboom)
    res = await _run()
    assert "import:ohlcv" in fake_daily
    assert res["sources"]["TAIEX"]["ok"] is False
    assert "TAIEX" in res["degraded"]


async def test_tdcc_failure_does_not_abort_core_import(fake_daily, monkeypatch):
    monkeypatch.setattr(daily.tdcc_conn, "fetch_shareholder_distribution", _aboom)
    res = await _run()
    assert "import:ohlcv" in fake_daily and "import:tpex_ohlcv" in fake_daily
    assert "TDCC" in res["degraded"]


async def test_tpex_margin_failure_keeps_ohlcv_and_institutional(fake_daily, monkeypatch):
    monkeypatch.setattr(daily.tpex_conn, "fetch_margin", _aboom)
    res = await _run()
    assert "import:tpex_ohlcv" in fake_daily
    assert "import:tpex_institutional" in fake_daily
    assert "import:tpex_margin" not in fake_daily
    assert res["sources"]["TPEx 融資券"]["ok"] is False
    assert res["sources"]["TPEx 行情"]["rows"] == 10


async def test_core_twse_failure_still_raises(fake_daily, monkeypatch):
    monkeypatch.setattr(daily.twse_conn, "fetch_ohlcv", _aboom)
    with pytest.raises(RuntimeError):
        await _run()
