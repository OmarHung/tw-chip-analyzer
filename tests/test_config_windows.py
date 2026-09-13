"""docs/12 Commit D：剩餘策略／統計視窗 config 化（模組常數不算 config 化）。

- 以覆寫過的 Thresholds 證明函式實際讀 config。
- 預設 config 下數值與修正前一致（YAML 列出與原常數相同的值）。
- 原本代表門檻的模組常數不得殘留。
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pandas as pd

from app.core.config import Thresholds, get_thresholds, load_yaml_thresholds
from app.core.threshold_registry import _with_path
from app.services import forward_report
from app.services.feature_builder import _price_features
from app.services.market_score import _regime_inputs

REPO = Path(__file__).resolve().parents[1]


def _t(**overrides) -> Thresholds:
    data = load_yaml_thresholds()
    for key, value in overrides.items():
        data = _with_path(data, key.replace("__", "."), value)
    return Thresholds(data)


def _bars(n: int) -> pd.DataFrame:
    base = dt.date(2026, 6, 1)
    return pd.DataFrame({
        "data_date": [base + dt.timedelta(days=i) for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + (i % 3) for i in range(n)],
        "close": [100.0 + i for i in range(n)],
        "volume": [1_000_000] * n,
        "turnover": [1e8] * n,
    })


def test_yaml_lists_all_windows_with_previous_values():
    y = load_yaml_thresholds()
    for k, v in {"flow_window_bars": 5, "ma_bars": 20, "atr_period": 14, "swing_low_bars": 10,
                 "sbl_lookback_bars": 20, "industry_min_members": 5}.items():
        assert y["features"][k] == v, k
    assert y["validation"]["min_ic_names"] == 20
    for k, v in {"ma_short_bars": 20, "ma_long_bars": 60, "slope_lookback_bars": 5,
                 "volatility_bars": 20}.items():
        assert y["market_regime"][k] == v, k


class TestFeatureWindowsReadConfig:
    def test_ma_bars(self):
        g = _bars(6)
        assert _price_features(g, thresholds=get_thresholds())["ma20"] is None
        assert _price_features(g, thresholds=_t(features__ma_bars=5))["ma20"] is not None

    def test_atr_period_requires_period_plus_one_bars(self):
        g = _bars(4)
        assert _price_features(g, thresholds=_t(features__atr_period=3))["atr14"] is not None
        assert _price_features(g, thresholds=_t(features__atr_period=4))["atr14"] is None

    def test_swing_low_bars(self):
        g = _bars(12)  # low 以 99,100,101 循環；最後 2 根為 100,101
        assert _price_features(g, thresholds=_t(features__swing_low_bars=2))["recent_swing_low"] == 100.0
        assert _price_features(g, thresholds=get_thresholds())["recent_swing_low"] == 99.0

    def test_flow_window_for_ret5(self):
        g = _bars(3)
        assert _price_features(g, thresholds=get_thresholds())["_ret5"] is None
        assert _price_features(g, thresholds=_t(features__flow_window_bars=2))["_ret5"] is not None


def test_market_regime_windows_read_config():
    close = pd.Series([100.0 + i for i in range(30)])
    ma20, ma60, slope, vol = _regime_inputs(close, get_thresholds())
    assert ma20 is not None and ma60 is None
    assert _regime_inputs(close, _t(market_regime__ma_long_bars=25))[1] is not None
    tiny = _regime_inputs(close.head(8), _t(market_regime__ma_short_bars=3,
                                            market_regime__slope_lookback_bars=2,
                                            market_regime__volatility_bars=3))
    assert tiny[0] is not None and tiny[2] is not None and tiny[3] is not None


def test_forward_report_min_ic_names_reads_config():
    assert forward_report._min_ic_names(get_thresholds()) == 20
    assert forward_report._min_ic_names(_t(validation__min_ic_names=7)) == 7


def test_no_hardcoded_window_constants_left():
    pattern = re.compile(
        r"^(SBL_LOOKBACK|FLOW_WINDOW|MA_BARS|ATR_BARS|_MIN_INDUSTRY_MEMBERS|_MIN_IC_NAMES)\s*=",
        re.M,
    )
    offenders = [
        p.name for p in (REPO / "app" / "services").glob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
