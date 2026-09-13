"""docs/09 第 3 批：驗證與掃描口徑。

- 截尾：單一離群值不可把整個橫斷面 z 壓扁（2026-09-08 實測 8444 外資 z=44.1）。
- BUG-12：漲跌家數以還原後前收比較，除息不算下跌。
- BUG-15：回測進場必須是市場次一交易日，停牌後復牌不算。
- BUG-05：前瞻驗證用還原價、Newey–West t，與離線 backtest 同口徑；新增價格即刷新。
"""
from __future__ import annotations

import datetime as dt

from app.backtest.engine import BacktestEngine, BacktestSignal
from app.backtest.forward_returns import Bar
from app.backtest.runner import market_calendar
from app.db.models.features import SignalSnapshot
from app.db.models.market import CorporateAction, DailyPrice, MarketIndex, Stock
from app.importers.base import availability_for
from app.services.feature_builder import _zscore_map


def _d(day: int) -> dt.date:
    return dt.date(2026, 1, 1) + dt.timedelta(days=day)


def _bar(day: int, px: float) -> Bar:
    return Bar(_d(day), px, px, px, px)


class TestWinsorizedZscore:
    def test_single_outlier_does_not_flatten_others(self):
        vals = {f"S{i}": float(i % 7 - 3) for i in range(200)}
        vals["OUT"] = 1e6
        z = _zscore_map(vals)
        spread = [abs(v) for k, v in z.items() if k != "OUT"]
        assert max(spread) > 1.0  # 未截尾時全部 < 0.01
        assert z["OUT"] == max(z.values())  # 保序
        assert abs(z["OUT"]) <= 5.0  # z 上限

    def test_order_preserved(self):
        z = _zscore_map({"A": 1.0, "B": 2.0, "C": 3.0})
        assert z["A"] < z["B"] < z["C"]


class TestEntryUsesMarketCalendar:
    def test_resumption_after_halt_is_not_entry(self):
        cal = [_d(i) for i in range(10)]
        # 訊號日 day1，該檔 day2~day6 停牌，day7 復牌
        bars = [_bar(0, 100), _bar(1, 100), _bar(7, 120), _bar(8, 121)]
        eng = BacktestEngine(calendar=cal)
        assert eng.evaluate_signal(BacktestSignal("X", _d(1), 80), bars) is None

    def test_normal_next_day_entry(self):
        cal = [_d(i) for i in range(10)]
        bars = [_bar(i, 100 + i) for i in range(10)]
        oc = BacktestEngine(calendar=cal).evaluate_signal(BacktestSignal("X", _d(1), 80), bars)
        assert oc is not None and oc.forward.entry_date == _d(2)

    def test_run_derives_calendar_from_all_prices(self):
        prices = {
            "A": [_bar(i, 100) for i in range(10)],
            "H": [_bar(0, 50), _bar(1, 50), _bar(7, 60), _bar(8, 60), _bar(9, 60)],
        }
        assert market_calendar(prices) == [_d(i) for i in range(10)]
        report = BacktestEngine().run(
            [BacktestSignal("A", _d(1), 80), BacktestSignal("H", _d(1), 80)], prices
        )
        assert report.evaluated == 1 and report.dropped == 1


async def test_breadth_uses_adjusted_prev_close(db_session):
    from app.db.models.market import MarketDaily
    from app.services.market_score import build_market_daily

    target, prev = dt.date(2026, 6, 30), dt.date(2026, 6, 29)
    for i in range(3):
        d = target - dt.timedelta(days=i)
        db_session.add(MarketIndex(data_date=d, available_at=availability_for(d), taiex_close=15000))
    for sym in ("DIV", "UP"):
        db_session.add(Stock(symbol=sym, name=sym, market="TWSE"))
    await db_session.flush()
    # DIV：前收 100，除息參考價 90（因子 0.9），今收 91 → 相對參考價上漲
    for sym, p0, p1 in (("DIV", 100, 91), ("UP", 50, 51)):
        for d, c in ((prev, p0), (target, p1)):
            db_session.add(DailyPrice(symbol=sym, data_date=d, available_at=availability_for(d),
                                      open=c, high=c, low=c, close=c, volume=1000, turnover=c * 1000))
    db_session.add(CorporateAction(symbol="DIV", data_date=target, available_at=availability_for(prev),
                                   kind="息", prev_close=100, reference_price=90, adj_factor=0.9))
    await db_session.commit()

    assert await build_market_daily(db_session, target)
    md = await db_session.get(MarketDaily, 1)
    assert md.advancers == 2 and md.decliners == 0


async def _seed_forward(session, split: bool):
    """兩檔、30 個交易日。SPL 在 day10 1 拆 2（價格腰斬，因子 0.5），實際報酬 0。"""
    for sym in ("SPL", "FLT"):
        session.add(Stock(symbol=sym, name=sym, market="TWSE"))
    await session.flush()
    for i in range(30):
        d = _d(i)
        for sym in ("SPL", "FLT"):
            px = 50.0 if (sym == "SPL" and split and i >= 10) else 100.0
            session.add(DailyPrice(symbol=sym, data_date=d, available_at=availability_for(d),
                                   open=px, high=px, low=px, close=px, volume=1000, turnover=px * 1000))
    if split:
        session.add(CorporateAction(symbol="SPL", data_date=_d(10), available_at=availability_for(_d(9)),
                                    kind="面額", prev_close=100, reference_price=50, adj_factor=0.5))
    for i in range(20):
        for sym, sc in (("SPL", 80.0), ("FLT", 60.0)):
            session.add(SignalSnapshot(symbol=sym, data_date=_d(i), available_at=availability_for(_d(i)),
                                       chip_score=sc, action="WATCH"))
    await session.commit()


async def test_forward_report_uses_adjusted_prices(db_session):
    from app.services import forward_report

    forward_report._cache.clear()
    await _seed_forward(db_session, split=True)
    rep = await forward_report.build_forward_report(db_session)
    # h=5：訊號日 day5~day8 的持有期跨過 day10 拆股（h=1 進出場都在同側，測不出來）
    h5 = next(h for h in rep["horizons"] if h["horizon"] == 5)
    hi = next(b for b in h5["buckets"] if b["label"] == "[80,90)")
    # 拆股日不可出現 -50% 的假報酬：扣成本後應接近 0
    assert hi["avg_net"] > -0.01


async def test_forward_report_matches_offline_engine(db_session):
    from app.backtest.runner import run_db_backtest
    from app.services import forward_report

    forward_report._cache.clear()
    await _seed_forward(db_session, split=True)
    rep = await forward_report.build_forward_report(db_session)
    offline = await run_db_backtest(db_session)
    for h in (1, 5):
        online = next(x for x in rep["horizons"] if x["horizon"] == h)
        for b in online["buckets"]:
            ob = next(x for x in offline.by_bucket if x.label == b["label"])
            assert b["n"] == ob.by_horizon[h].count, (h, b["label"])
            if b["n"]:
                assert abs(b["avg_net"] - ob.by_horizon[h].avg_return) < 1e-4


def test_forward_ic_t_uses_newey_west(monkeypatch):
    from app.services import forward_report

    seen: list[int] = []

    def fake_nw(values, lags):
        seen.append(lags)
        return 1.23

    monkeypatch.setattr(forward_report, "newey_west_t", fake_nw)
    out = forward_report._ic_stats([0.1, 0.2, 0.05, 0.15], horizon=5)
    assert out["ic_t"] == 1.23 and seen == [4]
    assert out["ic_t_naive"] is not None  # 樸素 t 僅留作對照膨脹幅度


async def test_forward_report_refreshes_on_new_prices(db_session):
    from app.services import forward_report

    forward_report._cache.clear()
    await _seed_forward(db_session, split=False)
    before = await forward_report.build_forward_report(db_session)
    d = _d(30)
    for sym in ("SPL", "FLT"):
        db_session.add(DailyPrice(symbol=sym, data_date=d, available_at=availability_for(d),
                                  open=100, high=100, low=100, close=100, volume=1000, turnover=1e5))
    await db_session.commit()
    after = await forward_report.build_forward_report(db_session)
    h20b = next(h for h in before["horizons"] if h["horizon"] == 20)
    h20a = next(h for h in after["horizons"] if h["horizon"] == 20)
    assert h20a["n"] > h20b["n"]  # 最新訊號日沒變，但多一天價格 → 多實現樣本
