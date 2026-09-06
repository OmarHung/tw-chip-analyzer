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
    obi: float = 0.0
    absorption_z: float = 0.0
    trade_speed_z: float = 0.0
    price_efficiency_z: float = 0.0

@dataclass
class DailyFeatures:
    foreign_5d_z: float = 0.0
    trust_5d_z: float = 0.0
    dealer_5d_z: float = 0.0
    margin_balance_change_z: float = 0.0
    short_balance_change_z: float = 0.0
    sbl_change_z: float = 0.0
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
    market_trend_score: float = 0.0
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
