"""開發環境示範資料 seed（寫入 twchip 開發庫）。

用途：手動驗證 API。執行：python -m scripts.seed_dev
"""
from __future__ import annotations

import asyncio
import datetime as dt

from sqlalchemy import delete

from app.db.models.features import FeatureDaily
from app.db.models.market import Stock
from app.db.session import get_sessionmaker

SAMPLE = [
    # symbol, name, industry, close, atr, turnover, foreign_z, trust_z, margin_z, large_holder_z, retail_z
    ("2330", "台積電", "半導體", 1000, 15, 50e9, 1.5, 2.0, -1.2, 1.6, -1.3),
    ("2317", "鴻海", "電子零組件", 100, 2, 8e9, -1.5, -1.0, 1.5, -1.2, 1.1),
    ("2454", "聯發科", "半導體", 1200, 25, 12e9, 1.0, 1.2, -0.5, 0.8, -0.6),
    ("2603", "長榮", "航運", 200, 6, 15e9, 0.3, -0.2, 0.4, 0.1, 0.0),
    ("2412", "中華電", "電信", 120, 1.2, 2e9, 0.5, 0.3, -0.3, 0.4, -0.2),
]


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        d = dt.date(2026, 9, 5)
        av = dt.datetime(2026, 9, 5, 15, 0)
        syms = [row[0] for row in SAMPLE]
        # 冪等：先清掉本組示範資料
        await s.execute(delete(FeatureDaily).where(FeatureDaily.symbol.in_(syms)))
        await s.execute(delete(Stock).where(Stock.symbol.in_(syms)))
        await s.flush()
        for sym, name, ind, close, atr, turnover, fz, tz, mz, lhz, rhz in SAMPLE:
            await s.merge(Stock(symbol=sym, name=name, market="TWSE", industry=ind))
            await s.flush()
            await s.merge(
                FeatureDaily(
                    symbol=sym, data_date=d, available_at=av,
                    close=close, atr14=atr, ma20=close * 0.98, vwap=close * 0.995,
                    recent_swing_low=close * 0.95, turnover=turnover,
                    close_vs_ma20_pct=0.02, close_vs_vwap_pct=0.005,
                    foreign_5d_z=fz, trust_5d_z=tz, dealer_5d_z=0.2,
                    margin_balance_change_z=mz, sbl_change_z=-0.2, short_balance_change_z=-0.1,
                    large_holder_ratio_change_z=lhz, retail_holder_ratio_change_z=rhz,
                    holder_count_change_z=-0.5,
                )
            )
        await s.commit()
    print(f"seeded {len(SAMPLE)} stocks into dev DB")


if __name__ == "__main__":
    asyncio.run(main())
