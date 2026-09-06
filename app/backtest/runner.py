"""DB-backed backtest runner：從 signal_snapshot + daily_price 讀取後回測。

Look-ahead：進場點由引擎以 data_date 之後第一根 bar 決定；此處只負責載入資料。
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.engine import BacktestEngine, BacktestReport, BacktestSignal
from app.backtest.forward_returns import Bar
from app.db.models.features import SignalSnapshot
from app.db.models.market import DailyPrice


async def load_signals(session: AsyncSession) -> list[BacktestSignal]:
    stmt = select(
        SignalSnapshot.symbol, SignalSnapshot.data_date, SignalSnapshot.chip_score
    ).order_by(SignalSnapshot.data_date)
    rows = (await session.execute(stmt)).all()
    return [BacktestSignal(sym, d, float(score)) for sym, d, score in rows]


async def load_bars(
    session: AsyncSession, symbols: list[str]
) -> dict[str, list[Bar]]:
    stmt = (
        select(
            DailyPrice.symbol,
            DailyPrice.data_date,
            DailyPrice.open,
            DailyPrice.high,
            DailyPrice.low,
            DailyPrice.close,
        )
        .where(DailyPrice.symbol.in_(symbols))
        .order_by(DailyPrice.symbol, DailyPrice.data_date)
    )
    out: dict[str, list[Bar]] = defaultdict(list)
    for sym, d, o, h, low, c in (await session.execute(stmt)).all():
        if None in (o, h, low, c):
            continue
        out[sym].append(Bar(d, float(o), float(h), float(low), float(c)))
    return dict(out)


async def run_db_backtest(
    session: AsyncSession, engine: BacktestEngine | None = None
) -> BacktestReport:
    engine = engine or BacktestEngine()
    signals = await load_signals(session)
    symbols = sorted({s.symbol for s in signals})
    prices = await load_bars(session, symbols)
    return engine.run(signals, prices)
