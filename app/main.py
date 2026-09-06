from app.models.signal import *
from app.services.scoring import ChipScorer
from app.services.decision import decide

def demo():
    intraday = IntradayFeatures(
        cvd_z=1.8,
        large_trade_delta_z=2.1,
        obi=0.28,
        absorption_z=1.3,
        trade_speed_z=1.0,
        price_efficiency_z=0.4,
    )
    daily = DailyFeatures(
        foreign_5d_z=0.8,
        trust_5d_z=1.4,
        dealer_5d_z=0.2,
        margin_balance_change_z=-1.0,
        short_balance_change_z=0.1,
        sbl_change_z=0.3,
    )
    weekly = WeeklyFeatures(
        large_holder_ratio_change_z=1.3,
        retail_holder_ratio_change_z=-1.1,
        holder_count_change_z=-0.8,
    )
    market = MarketContext(
        market_trend_score=0.4,
        industry_trend_score=0.6,
        volatility_percentile=0.5,
    )

    score, reasons = ChipScorer().score(intraday, daily, weekly, market)
    result = decide(
        score=score,
        reasons=reasons,
        last_price=100,
        atr14=2.2,
        recent_swing_low=95.5,
        already_in_position=False,
    )
    print(result)

if __name__ == "__main__":
    demo()
