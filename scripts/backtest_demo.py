"""Backtest 端到端示範（寫入 dev DB，BT 前綴符號，可重複執行）。

合成行情：日漂移隨 chip_score 提高而增加 + 隨機雜訊。
目的：驗證回測管線可跑，且「score 越高、forward return 越好」成立。
執行：APP_ENV=dev python -m scripts.backtest_demo
"""
from __future__ import annotations

import asyncio
import datetime as dt
import random

from sqlalchemy import delete

from app.backtest import BacktestEngine, format_bucket_table
from app.backtest.runner import run_db_backtest
from app.db.models.features import SignalSnapshot
from app.db.models.market import DailyPrice, Stock
from app.db.session import get_sessionmaker

SEED = 42
N_SYMBOLS = 200
N_BARS = 32
SIGNAL_BAR = 4  # data_date 落在第 5 根 bar → 進場為第 6 根，之後仍有 26 根供 20D


def _gen():
    rnd = random.Random(SEED)
    base = dt.date(2026, 1, 1)
    stocks, prices, signals = [], [], []
    for i in range(N_SYMBOLS):
        sym = f"BT{i:04d}"
        score = rnd.uniform(50, 95)
        # 日期望報酬隨 score 提高；加雜訊使 win rate < 100%
        daily_mu = (score - 60) / 10 * 0.0025
        stocks.append((sym, score))
        price = 100.0
        bars = []
        for d in range(N_BARS):
            o = price
            ret = daily_mu + rnd.gauss(0, 0.008)
            c = round(o * (1 + ret), 2)
            hi = round(max(o, c) * (1 + abs(rnd.gauss(0, 0.003))), 2)
            lo = round(min(o, c) * (1 - abs(rnd.gauss(0, 0.003))), 2)
            bars.append((base + dt.timedelta(days=d), o, hi, lo, c))
            price = c
        prices.append((sym, bars))
        sig_date = bars[SIGNAL_BAR][0]
        signals.append((sym, sig_date, score))
    return stocks, prices, signals


async def main() -> None:
    stocks, prices, signals = _gen()
    sm = get_sessionmaker()
    async with sm() as s:
        # 清掉先前 demo 資料
        syms = [x[0] for x in stocks]
        await s.execute(delete(SignalSnapshot).where(SignalSnapshot.symbol.in_(syms)))
        await s.execute(delete(DailyPrice).where(DailyPrice.symbol.in_(syms)))
        await s.execute(delete(Stock).where(Stock.symbol.in_(syms)))
        await s.flush()

        for sym, _score in stocks:
            s.add(Stock(symbol=sym, name=sym, market="TWSE", industry="DEMO"))
        await s.flush()

        for sym, bars in prices:
            for d, o, h, lo, c in bars:
                s.add(
                    DailyPrice(
                        symbol=sym, data_date=d,
                        available_at=dt.datetime(d.year, d.month, d.day, 14, 0),
                        open=o, high=h, low=lo, close=c, volume=1_000_000,
                        turnover=c * 1_000_000,
                    )
                )
        for sym, sig_date, score in signals:
            s.add(
                SignalSnapshot(
                    symbol=sym, data_date=sig_date,
                    available_at=dt.datetime(sig_date.year, sig_date.month, sig_date.day, 15, 0),
                    chip_score=score, action="WATCH",
                )
            )
        await s.commit()

        report = await run_db_backtest(s, BacktestEngine())

    print(format_bucket_table(report, horizon=5))
    print("\n=== Score Threshold × 5D（net avg）===")
    for br in report.by_threshold:
        st = br.by_horizon.get(5)
        if st and st.count:
            print(f"{br.label:<6} n={st.count:<4} avg={st.avg_return:>7.2%} win={st.win_rate:>5.0%} PF={st.profit_factor:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
