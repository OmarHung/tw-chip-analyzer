"""price_efficiency 純函數測試(方向性路徑效率 -1..1)。"""
from __future__ import annotations

from app.services.orderflow.price_efficiency import price_efficiency


def test_monotonic_up_is_one():
    assert price_efficiency([100, 101, 102, 103]) == 1.0


def test_monotonic_down_is_minus_one():
    assert price_efficiency([103, 102, 101, 100]) == -1.0


def test_choppy_nets_to_zero():
    # 來回震盪:淨位移 0、路徑長 → 效率 0
    assert price_efficiency([100, 102, 100, 102, 100]) == 0.0


def test_partial_efficiency():
    # net = +1;path = |103-100| + |101-103| = 3 + 2 = 5 → 0.2
    assert price_efficiency([100, 103, 101]) == 0.2


def test_too_short_or_flat_returns_zero():
    assert price_efficiency([]) == 0.0
    assert price_efficiency([100]) == 0.0
    assert price_efficiency([100, 100, 100]) == 0.0
