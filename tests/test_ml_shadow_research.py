"""scripts/ml_shadow_research 的純函式（docs/16 §B.1）——資料組裝/版本挑選不炸即可，
不驗證統計顯著性（那是跑腳本本身的事，不是單元測試的事）。"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from scripts.ml_shadow_research import (
    BASELINE_COL,
    FEATURE_COLS,
    build_feature_frame,
    regime_days,
    select_config_version,
    usable_features,
)


def test_select_config_version_prefers_current_when_present():
    version, matches = select_config_version({"a": 10, "b": 90}, "a")
    assert version == "a" and matches is True


def test_select_config_version_falls_back_to_largest_when_current_missing():
    version, matches = select_config_version({"a": 10, "b": 90}, "c")
    assert version == "b" and matches is False


def test_select_config_version_raises_on_empty():
    with pytest.raises(ValueError):
        select_config_version({}, "a")


def test_build_feature_frame_coerces_numeric_and_bool():
    rows = [
        {"symbol": "2330", "data_date": dt.date(2026, 9, 1), BASELINE_COL: 80.0,
         "foreign_5d_z": 1.2, "is_limit_locked": True, "market_trend_score": 0.4},
        {"symbol": "2317", "data_date": dt.date(2026, 9, 1), BASELINE_COL: 40.0,
         "foreign_5d_z": None, "is_limit_locked": False, "market_trend_score": 0.4},
    ]
    df = build_feature_frame(rows)
    assert len(df) == 2
    assert df["foreign_5d_z"].isna().sum() == 1
    assert df.loc[df["symbol"] == "2330", BASELINE_COL].iloc[0] == pytest.approx(80.0)
    # 原始價格/量水準欄位刻意不在候選特徵裡(CLAUDE.md 鐵則 6:跨股票不可直接比張數)
    assert "close" not in FEATURE_COLS
    assert "turnover" not in FEATURE_COLS
    assert "atr14" not in FEATURE_COLS


def test_build_feature_frame_empty_rows_returns_empty_df():
    df = build_feature_frame([])
    assert df.empty


def test_usable_features_drops_all_nan_columns():
    base = {c: 1.0 for c in FEATURE_COLS}
    base["trust_5d_z"] = None  # 這欄全部列都缺
    rows = []
    for i in range(3):
        row = dict(base)
        row.update(symbol=f"S{i}", data_date=dt.date(2026, 9, 1), **{BASELINE_COL: 50.0})
        rows.append(row)
    train_fit = build_feature_frame(rows)
    cols = usable_features(train_fit)
    assert "trust_5d_z" not in cols
    assert "foreign_5d_z" in cols


def test_regime_days_splits_by_threshold_and_excludes_nan():
    day_trend = pd.Series(
        {dt.date(2026, 9, 1): 0.5, dt.date(2026, 9, 2): 0.1, dt.date(2026, 9, 3): np.nan},
    )
    bull, non_bull = regime_days(day_trend, 0.3)
    assert bull == {dt.date(2026, 9, 1)}
    assert non_bull == {dt.date(2026, 9, 2)}
    assert dt.date(2026, 9, 3) not in bull | non_bull
