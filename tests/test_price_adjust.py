"""還原價（back-adjust）測試：純函式 + feature_builder + backtest 整合。"""
from __future__ import annotations

import datetime as dt

from app.backtest.runner import load_bars
from app.db.models.market import CorporateAction, DailyPrice, Stock
from app.importers.base import availability_for
from app.repositories.features import FeatureDailyRepository
from app.services.feature_builder import build_features
from app.services.price_adjust import back_adjust, cumulative_factors

TARGET = dt.date(2026, 3, 20)


class TestPureFunctions:
    def test_no_actions_is_identity(self):
        dates = [dt.date(2026, 3, d) for d in (1, 2, 3)]
        assert cumulative_factors(dates, []) == [1.0, 1.0, 1.0]
        assert back_adjust(dates, [10.0, 11.0, 12.0], []) == [10.0, 11.0, 12.0]

    def test_ex_date_only_adjusts_earlier_bars(self):
        # 除權息日 = 3/3、factor 0.9：3/1、3/2（<3/3）乘 0.9；3/3 當天不動。
        dates = [dt.date(2026, 3, d) for d in (1, 2, 3)]
        facs = cumulative_factors(dates, [(dt.date(2026, 3, 3), 0.9)])
        assert facs == [0.9, 0.9, 1.0]
        adj = back_adjust(dates, [100.0, 100.0, 90.0], [(dt.date(2026, 3, 3), 0.9)])
        assert adj == [90.0, 90.0, 90.0]  # 序列連續：斷點消失

    def test_invalid_factor_skipped(self):
        dates = [dt.date(2026, 3, 1), dt.date(2026, 3, 2)]
        assert cumulative_factors(dates, [(dt.date(2026, 3, 2), None)]) == [1.0, 1.0]
        assert cumulative_factors(dates, [(dt.date(2026, 3, 2), 0.0)]) == [1.0, 1.0]


async def _seed_two_symbols_with_drop(session):
    """兩檔在 TARGET 當天收盤由 100 跌到 90；只有 DIVX 有除權息事件。"""
    session.add_all(
        [
            Stock(symbol="DIVX", name="除息股", market="TWSE"),
            Stock(symbol="RAWX", name="對照股", market="TWSE"),
        ]
    )
    await session.flush()
    for i in range(25):
        d = TARGET - dt.timedelta(days=24 - i)
        av = availability_for(d)
        close = 90.0 if d == TARGET else 100.0
        for sym in ("DIVX", "RAWX"):
            session.add(
                DailyPrice(
                    symbol=sym, data_date=d, available_at=av,
                    open=close, high=close * 1.005, low=close * 0.995,
                    close=close, volume=1_000_000, turnover=close * 1_000_000,
                )
            )
    # 只有 DIVX 在 TARGET 除息：前收 100 → 參考 90（factor 0.9）
    session.add(
        CorporateAction(
            symbol="DIVX", data_date=TARGET, available_at=availability_for(TARGET),
            kind="息", prev_close=100, reference_price=90, value=10, adj_factor=0.9,
        )
    )
    await session.commit()


async def test_feature_builder_backadjusts_ex_dividend(db_session):
    await _seed_two_symbols_with_drop(db_session)
    await build_features(db_session, TARGET)
    repo = FeatureDailyRepository(db_session)

    div = await repo.get_latest("DIVX")
    raw = await repo.get_latest("RAWX")

    # 對照股（無還原）：當日報酬 ≈ -10%、明顯低於 MA20（假跌被當真跌）
    assert raw.change_pct < -0.09
    assert raw.close_vs_ma20_pct < -0.08

    # 除息股（已還原）：報酬 ≈ 0、貼近 MA20（斷點被還原消除）
    assert abs(float(div.change_pct)) < 1e-6
    assert abs(float(div.close_vs_ma20_pct)) < 1e-6


async def test_backtest_load_bars_backadjusts(db_session):
    await _seed_two_symbols_with_drop(db_session)
    bars = await load_bars(db_session, ["DIVX", "RAWX"])

    # DIVX 已還原：整段連續（前段 100→90、最後一根維持 90），報酬跨除息連續。
    div_closes = [b.close for b in bars["DIVX"]]
    assert div_closes[0] == 90.0 and div_closes[-1] == 90.0
    # RAWX 未還原：前段仍是原始 100、最後 90（斷點保留）。
    raw_closes = [b.close for b in bars["RAWX"]]
    assert raw_closes[0] == 100.0 and raw_closes[-1] == 90.0
