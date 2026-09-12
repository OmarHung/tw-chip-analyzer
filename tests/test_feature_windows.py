"""docs/09 第 2 批：固定視窗特徵資料不足必須為 NULL，缺資料不可冒充中性 0。

- BUG-10：ret5 / ma20 / atr14 / avg_vol20 / 法人 5 日 / 融資券 5 日 / 借券 20 日，
  觀測數不足時回 None，而非用最早一筆湊出名義上的 5/20 日值。
- BUG-11：法人/信用/借券 z 缺值維持 NULL；institutional 子項全缺時排除整個成分，
  部分缺時在成分內依權重重分配。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.db.models.features import FeatureDaily
from app.models.signal import DailyFeatures
from app.services.analysis import AnalysisService
from app.services.chip.institutional_score import institutional_score
from app.services.feature_builder import (
    _asof_change,
    _net_window,
    _price_features,
    _zscore_map,
)

BASE = dt.date(2026, 6, 1)


def _days(n: int) -> list[dt.date]:
    return [BASE + dt.timedelta(days=i) for i in range(n)]


def _bars(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "data_date": _days(n),
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.0 + i for i in range(n)],
            "volume": [1_000_000] * n,
            "turnover": [1e8] * n,
        }
    )


class TestPriceWindows:
    def test_two_bars_give_null_window_features(self):
        f = _price_features(_bars(2))
        assert f["_ret5"] is None
        assert f["_avg_vol20"] is None
        assert f["ma20"] is None and f["close_vs_ma20_pct"] is None
        assert f["atr14"] is None
        assert f["change_pct"] is not None  # 單日漲跌只需 2 根

    def test_full_history_gives_values(self):
        f = _price_features(_bars(25))
        assert f["_ret5"] is not None and f["_avg_vol20"] is not None
        assert f["ma20"] is not None and f["atr14"] is not None


class TestFlowWindows:
    def test_net_window_only_sums_last_market_days(self):
        days = _days(10)
        # 該檔只有第 0 天（窗外，一週多前）與最後一天有法人列
        g = pd.DataFrame({"data_date": [days[0], days[-1]], "foreign_net": [999, 5]})
        assert _net_window(g, "foreign_net", days[-5:]) == 5

    def test_net_window_none_without_rows_in_window(self):
        days = _days(10)
        g = pd.DataFrame({"data_date": [days[0]], "foreign_net": [999]})
        assert _net_window(g, "foreign_net", days[-5:]) is None

    def test_asof_change_requires_base_before_lookback(self):
        days = _days(10)
        g = pd.DataFrame({"data_date": days[-2:], "bal": [100, 150]})
        assert _asof_change(g, "bal", days, lookback=5) is None  # 只有 2 筆
        g = pd.DataFrame({"data_date": days, "bal": [100 + i * 10 for i in range(10)]})
        assert _asof_change(g, "bal", days, lookback=5) == 50

    def test_asof_change_requires_latest_on_target(self):
        days = _days(10)
        g = pd.DataFrame({"data_date": days[:-1], "bal": list(range(9))})
        assert _asof_change(g, "bal", days, lookback=5) is None

    def test_asof_change_pct(self):
        days = _days(25)
        g = pd.DataFrame({"data_date": days, "bal": [100] * 5 + [200] * 20})
        assert _asof_change(g, "bal", days, lookback=20, pct=True) == 1.0


class TestZscoreMap:
    def test_single_sample_is_undefined(self):
        assert _zscore_map({"A": 3.0}) == {}

    def test_identical_values_are_truly_neutral(self):
        assert _zscore_map({"A": 1.0, "B": 1.0}) == {"A": 0.0, "B": 0.0}


W = {
    "trust": 0.30, "foreign": 0.25, "dealer": 0.10,
    "margin_change": -0.15, "sbl_change": 0.0, "short_change": -0.10,
}


class TestInstitutionalAvailability:
    def test_all_missing_returns_none(self):
        f = DailyFeatures(
            foreign_5d_z=None, trust_5d_z=None, dealer_5d_z=None,
            margin_balance_change_z=None, short_balance_change_z=None, sbl_change_z=None,
        )
        assert institutional_score(f, W) is None

    def test_only_zero_weight_item_present_counts_as_missing(self):
        f = DailyFeatures(
            foreign_5d_z=None, trust_5d_z=None, dealer_5d_z=None,
            margin_balance_change_z=None, short_balance_change_z=None, sbl_change_z=2.0,
        )
        assert institutional_score(f, W) is None

    def test_partial_missing_redistributes_weights(self):
        full = DailyFeatures(
            foreign_5d_z=1.0, trust_5d_z=1.0, dealer_5d_z=1.0,
            margin_balance_change_z=-1.0, short_balance_change_z=-1.0, sbl_change_z=0.0,
        )
        only_trust = DailyFeatures(
            foreign_5d_z=None, trust_5d_z=1.0, dealer_5d_z=None,
            margin_balance_change_z=None, short_balance_change_z=None, sbl_change_z=None,
        )
        # 全部子項同向同強度 → 重分配後與完整資料同分（尺度不因缺子項縮水）
        assert abs(institutional_score(full, W) - institutional_score(only_trust, W)) < 1e-9


def _fd(sym: str, **kw) -> FeatureDaily:
    base = dict(
        symbol=sym, data_date=dt.date(2026, 9, 4),
        available_at=dt.datetime(2026, 9, 4, 15, 0),
        close=100, atr14=2, ma20=100, vwap=100, recent_swing_low=95,
        turnover=5e8, close_vs_ma20_pct=0.0, close_vs_vwap_pct=0.0,
    )
    base.update(kw)
    return FeatureDaily(**base)


class TestAnalysisInstitutionalComponent:
    def test_no_institutional_data_excludes_component(self):
        r = AnalysisService().score_features(_fd("A"))
        assert "institutional" not in r.components

    def test_institutional_data_includes_component(self):
        r = AnalysisService().score_features(_fd("A", foreign_5d_z=1.2))
        assert "institutional" in r.components
        assert "外資近5日買超強度偏高" in r.reasons  # None 子項不可讓 reasons 比較炸掉
