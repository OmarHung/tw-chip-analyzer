"""逐筆服務：DB 快取優先，未命中則向 Shioaji（simulation 行情）抓取並存入 raw_tick。"""
from __future__ import annotations

import asyncio
import datetime as dt

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.shioaji_market import fetch_ticks_sync
from app.core.logging import get_logger
from app.db.models.intraday import RawTick
from app.db.models.market import Stock
from app.repositories.upsert import upsert_ignore

logger = get_logger("services.ticks")


def _to_epoch(when: dt.datetime) -> int:
    """把台北當地時間（naive）視為 UTC 秒，使前端以 UTC 顯示即為台北時間。"""
    return int(when.replace(tzinfo=dt.timezone.utc).timestamp())


def _row_to_api(ts: dt.datetime, price, volume, bid, ask, side) -> dict:
    return {
        "t": _to_epoch(ts),
        "time": ts.strftime("%H:%M:%S"),
        "price": float(price),
        "volume": int(volume),
        "side": int(side) if side is not None else 0,
        "bid": float(bid) if bid is not None else None,
        "ask": float(ask) if ask is not None else None,
    }


async def get_ticks(session: AsyncSession, symbol: str, date: dt.date) -> list[dict]:
    # 1) DB 快取
    cached = (
        await session.execute(
            select(RawTick)
            .where(RawTick.symbol == symbol, RawTick.data_date == date)
            .order_by(RawTick.ts)
        )
    ).scalars().all()
    if cached:
        return [
            _row_to_api(r.ts, r.price, r.volume, r.bid_price, r.ask_price, r.aggressor_side)
            for r in cached
        ]

    # 2) 向 Shioaji 抓取（同步 API 於 thread 執行，避免阻塞事件迴圈）
    try:
        ticks = await asyncio.to_thread(fetch_ticks_sync, symbol, date)
    except Exception as e:
        logger.warning("Shioaji 逐筆抓取失敗 %s %s: %s", symbol, date, e)
        return []
    if not ticks:
        return []

    # 3) 存入 raw_tick（FK 保護 + 先清當日再插，維持冪等）
    await upsert_ignore(
        session, Stock, [{"symbol": symbol, "name": symbol, "market": "TWSE"}], ["symbol"]
    )
    await session.execute(
        delete(RawTick).where(RawTick.symbol == symbol, RawTick.data_date == date)
    )
    session.add_all(
        [
            RawTick(
                symbol=symbol,
                data_date=date,
                ts=t["ts"],
                price=t["price"],
                volume=t["volume"],
                bid_price=t["bid"],
                ask_price=t["ask"],
                aggressor_side=t["side"],
            )
            for t in ticks
        ]
    )
    await session.commit()

    return [
        _row_to_api(t["ts"], t["price"], t["volume"], t["bid"], t["ask"], t["side"])
        for t in ticks
    ]
