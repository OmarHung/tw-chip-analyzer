"""FeatureDaily / SignalSnapshot 的資料存取。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.features import FeatureDaily, SignalSnapshot


class FeatureDailyRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_latest(self, symbol: str) -> FeatureDaily | None:
        stmt = (
            select(FeatureDaily)
            .where(FeatureDaily.symbol == symbol)
            .order_by(FeatureDaily.data_date.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_on_or_before(
        self, symbol: str, as_of: dt.date
    ) -> FeatureDaily | None:
        """Look-ahead 安全：只取 data_date <= as_of 的最新一筆。"""
        stmt = (
            select(FeatureDaily)
            .where(FeatureDaily.symbol == symbol, FeatureDaily.data_date <= as_of)
            .order_by(FeatureDaily.data_date.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_on_date(self, data_date: dt.date) -> list[FeatureDaily]:
        stmt = select(FeatureDaily).where(FeatureDaily.data_date == data_date)
        return list((await self.session.execute(stmt)).scalars().all())

    async def latest_date(self) -> dt.date | None:
        stmt = select(FeatureDaily.data_date).order_by(
            FeatureDaily.data_date.desc()
        ).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class SignalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, snapshot: SignalSnapshot) -> SignalSnapshot:
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot
