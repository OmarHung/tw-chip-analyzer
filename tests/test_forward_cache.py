"""docs/12 Commit C：前瞻驗證快取對「同筆更新」可靠失效 + pending 定義。

重建（upsert 覆寫同日期同筆數）、修正既有價格、更新公司行動因子時，日期與筆數都不變；
舊的 max(date)/count/created_at 快取 key 偵測不到，API 會一直回舊報告。
"""
from __future__ import annotations

import datetime as dt

from app.db.models.features import SignalSnapshot
from app.db.models.market import CorporateAction, DailyPrice, Stock
from app.importers.base import availability_for
from app.repositories.upsert import upsert_many
from app.services import forward_report

BT = {"horizons": [1, 5]}
REPORT_CFG = {"backtest": BT, "validation": {"min_ic_names": 20}}


def _d(i: int) -> dt.date:
    return dt.date(2026, 1, 1) + dt.timedelta(days=i)


async def _seed(session, days: int = 12):
    for sym in ("AAA", "BBB"):
        session.add(Stock(symbol=sym, name=sym, market="TWSE"))
    await session.flush()
    for i in range(days):
        for sym in ("AAA", "BBB"):
            session.add(DailyPrice(symbol=sym, data_date=_d(i), available_at=availability_for(_d(i)),
                                   open=100, high=100, low=100, close=100, volume=1000, turnover=1e5))
        for sym, sc in (("AAA", 80.0), ("BBB", 55.0)):
            session.add(SignalSnapshot(symbol=sym, data_date=_d(i), available_at=availability_for(_d(i)),
                                       chip_score=sc, action="WATCH"))
    session.add(CorporateAction(symbol="AAA", data_date=_d(3), available_at=availability_for(_d(2)),
                                kind="息", prev_close=100, reference_price=99, adj_factor=0.99))
    await session.commit()


async def test_snapshot_same_key_upsert_changes_cache_key(db_session):
    await _seed(db_session)
    k0 = await forward_report._cache_key(db_session, REPORT_CFG)
    await upsert_many(db_session, SignalSnapshot, [
        {"symbol": "AAA", "data_date": _d(0), "available_at": availability_for(_d(0)),
         "chip_score": 55.0, "action": "HOLD"},
    ], ["symbol", "data_date"])
    await db_session.commit()
    assert await forward_report._cache_key(db_session, REPORT_CFG) != k0


async def test_price_same_key_fix_changes_cache_key(db_session):
    await _seed(db_session)
    k0 = await forward_report._cache_key(db_session, REPORT_CFG)
    await upsert_many(db_session, DailyPrice, [
        {"symbol": "AAA", "data_date": _d(5), "available_at": availability_for(_d(5)),
         "open": 101, "high": 101, "low": 101, "close": 101, "volume": 1000, "turnover": 1e5},
    ], ["symbol", "data_date"])
    await db_session.commit()
    assert await forward_report._cache_key(db_session, REPORT_CFG) != k0


async def test_corporate_action_factor_update_changes_cache_key(db_session):
    await _seed(db_session)
    k0 = await forward_report._cache_key(db_session, REPORT_CFG)
    await upsert_many(db_session, CorporateAction, [
        {"symbol": "AAA", "data_date": _d(3), "available_at": availability_for(_d(2)),
         "kind": "息", "prev_close": 100, "reference_price": 98, "adj_factor": 0.98},
    ], ["symbol", "data_date"])
    await db_session.commit()
    assert await forward_report._cache_key(db_session, REPORT_CFG) != k0


async def test_report_refreshes_after_rebuild_without_restart(db_session):
    forward_report._cache.clear()
    await _seed(db_session)
    before = await forward_report.build_forward_report(db_session)
    rows = [
        {"symbol": s, "data_date": _d(i), "available_at": availability_for(_d(i)),
         "chip_score": 95.0 if s == "BBB" else 52.0, "action": "WATCH"}
        for i in range(12) for s in ("AAA", "BBB")
    ]
    await upsert_many(db_session, SignalSnapshot, rows, ["symbol", "data_date"])  # 同日期同筆數
    await db_session.commit()
    after = await forward_report.build_forward_report(db_session)

    def labels(rep):
        return {b["label"]: b["n"] for b in rep["horizons"][0]["buckets"]}

    assert labels(before) != labels(after)


async def test_pending_counts_latest_signals_without_next_trading_day(db_session):
    forward_report._cache.clear()
    await _seed(db_session)
    rep = await forward_report.build_forward_report(db_session)
    # 最後一天（day11）的 2 筆訊號尚無下一交易日 → 等待進場
    assert rep["pending_entry"] == 2
    # 有進場但最短 horizon（1D＝進場當根收盤）出場價尚未出現 → 0
    assert rep["pending_exit_min_horizon"] == 0


# ---- docs/13 Phase 3：cache key 涵蓋報告實際讀取的全部設定 ----

def _thresholds(**overrides):
    """以真實門檻為底，深層覆寫指定路徑（如 validation__min_ic_names=10）。"""
    import copy

    from app.core.config import Thresholds, get_thresholds

    data = copy.deepcopy(get_thresholds().raw)
    for path, value in overrides.items():
        *parents, leaf = path.split("__")
        node = data
        for k in parents:
            node = node.setdefault(k, {})
        node[leaf] = value
    return Thresholds(data)


async def test_min_ic_names_change_changes_cache_key(db_session):
    await _seed(db_session)
    k20 = await forward_report._cache_key(
        db_session, forward_report._report_config(_thresholds(validation__min_ic_names=20)))
    k10 = await forward_report._cache_key(
        db_session, forward_report._report_config(_thresholds(validation__min_ic_names=10)))
    assert k20 != k10


async def test_same_data_and_config_hits_same_key(db_session):
    await _seed(db_session)
    t = _thresholds()
    k1 = await forward_report._cache_key(db_session, forward_report._report_config(t))
    k2 = await forward_report._cache_key(db_session, forward_report._report_config(_thresholds()))
    assert k1 == k2


async def test_backtest_settings_change_cache_key(db_session):
    await _seed(db_session)
    base = await forward_report._cache_key(
        db_session, forward_report._report_config(_thresholds()))
    for path, value in (
        ("backtest__costs__fee_rate", 0.002),
        ("backtest__horizons", [1, 2]),
        ("backtest__entry_price", "close"),
        ("backtest__score_buckets", [[0, 101]]),
    ):
        k = await forward_report._cache_key(
            db_session, forward_report._report_config(_thresholds(**{path: value})))
        assert k != base, path


async def test_report_recomputes_after_min_ic_names_reload(db_session, monkeypatch):
    forward_report._cache.clear()
    await _seed(db_session)
    # AAA 上漲、BBB 持平 → 每日橫斷面報酬有差異，IC 有定義
    await upsert_many(db_session, DailyPrice, [
        {"symbol": "AAA", "data_date": _d(i), "available_at": availability_for(_d(i)),
         "open": 100 + i, "high": 100 + i, "low": 100 + i, "close": 100 + i,
         "volume": 1000, "turnover": 1e5}
        for i in range(12)
    ], ["symbol", "data_date"])
    await db_session.commit()

    monkeypatch.setattr(forward_report, "get_thresholds",
                        lambda: _thresholds(validation__min_ic_names=100))
    strict = await forward_report.build_forward_report(db_session)
    assert all(h["ic_days"] == 0 for h in strict["horizons"])

    # 只改 validation.min_ic_names（資料與 backtest 設定不變）→ 必須重算
    monkeypatch.setattr(forward_report, "get_thresholds",
                        lambda: _thresholds(validation__min_ic_names=2))
    loose = await forward_report.build_forward_report(db_session)
    assert any(h["ic_days"] > 0 for h in loose["horizons"])
