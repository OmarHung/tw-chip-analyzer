from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

class Action(str, Enum):
    BUY = "BUY"
    WATCH = "WATCH"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    AVOID = "AVOID"

@dataclass
class IntradayFeatures:
    cvd_z: float = 0.0
    large_trade_delta_z: float = 0.0
    cvd_slope_norm: float = 0.0  # CVD 斜率（每分鐘量正規化，-1..1）；非 OBI
    absorption_z: float = 0.0
    trade_speed_z: float = 0.0
    price_efficiency_z: float = 0.0

@dataclass
class DailyFeatures:
    # None = 無資料（窗不足/來源缺漏），與真正中性 0 不同（docs/09 BUG-11）
    foreign_5d_z: Optional[float] = 0.0
    trust_5d_z: Optional[float] = 0.0
    dealer_5d_z: Optional[float] = 0.0
    margin_balance_change_z: Optional[float] = 0.0
    short_balance_change_z: Optional[float] = 0.0
    sbl_change_z: Optional[float] = 0.0
    close_vs_vwap_pct: float = 0.0
    breakout_20d: bool = False
    avg_turnover_20d: float = 0.0

@dataclass
class WeeklyFeatures:
    large_holder_ratio_change_z: float = 0.0
    retail_holder_ratio_change_z: float = 0.0
    holder_count_change_z: float = 0.0

@dataclass
class MarketContext:
    # None = 目標日無大盤資料（未知）。不可用 0 冒充已知中性，也不可沿用前一日（docs/12 Phase 1）
    market_trend_score: Optional[float] = None
    industry_trend_score: float = 0.0
    volatility_percentile: float = 0.5

@dataclass
class SignalResult:
    score: float
    action: Action
    reasons: List[str]
    entry_zone: Optional[tuple[float, float]]
    stop_loss: Optional[float]
    take_profit_1: Optional[float]
    take_profit_2: Optional[float]
    risk_reward: Optional[float]
    rr_basis: Optional[str] = None   # resistance / breakout_atr / unavailable
    rr_target: Optional[float] = None
