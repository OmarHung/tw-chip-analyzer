"""每日 composite 分數落地 signal_snapshot(供 backtest / ML / 事後追蹤)。

daily job 建完 feature_daily 後呼叫:讀當日 feature + 大盤脈絡,逐檔 analyze,
帶 available_at(look-ahead 安全)upsert。payload 存完整 feature 快照供未來 ML。

單一真相來源:daily job(每日一天)與 rebuild_signals(歷史批次)共用本函式。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.features import FeatureDaily, SignalSnapshot
from app.db.models.market import Stock
from app.importers.base import availability_for
from app.repositories.market import load_market_context
from app.repositories.upsert import upsert_many
from app.services.analysis import AnalysisService

# feature 快照排除的欄位(識別/時間戳,非特徵)
_PAYLOAD_SKIP = {"id", "symbol", "data_date", "available_at", "created_at", "updated_at"}


def _feature_payload(fd: FeatureDaily) -> dict:
    """FeatureDaily → JSON-safe 特徵快照(供 Phase 2 ML / debug)。"""
    out: dict = {}
    for attr in inspect(fd).mapper.column_attrs:
        k = attr.key
        if k in _PAYLOAD_SKIP:
            continue
        v = getattr(fd, k)
        out[k] = float(v) if isinstance(v, Decimal) else v
    return out


def _snapshot_row(r, t: dt.date, av_at: dt.datetime, payload: dict) -> dict:
    chip, sig = r.chip, r.signal
    ez = sig.entry_zone
    return {
        "symbol": r.symbol,
        "data_date": t,
        "available_at": av_at,
        "chip_score": chip.chip_score,
        "intraday_score": chip.intraday,
        "institutional_score": chip.institutional,
        "holder_score": chip.holder,
        "market_score": chip.market,
        "action": sig.action.value,
        "entry_low": ez[0] if ez else None,
        "entry_high": ez[1] if ez else None,
        "stop_loss": sig.stop_loss,
        "tp1": sig.take_profit_1,
        "tp2": sig.take_profit_2,
        "risk_reward": sig.risk_reward,
        "reasons": sig.reasons,
        "payload": payload,
    }


async def persist_signals(session: AsyncSession, target: dt.date) -> int:
    """對 target 當日已建好的 feature_daily 逐檔算分並 upsert signal_snapshot。

    前置:feature_daily(target) 已存在(daily job 的 build_features 已跑)。
    回傳落地筆數;當日無 feature 時回 0。
    """
    stmt = (
        select(FeatureDaily, Stock.name)
        .join(Stock, Stock.symbol == FeatureDaily.symbol)
        .where(FeatureDaily.data_date == target)
    )
    pairs = (await session.execute(stmt)).all()
    if not pairs:
        return 0
    market = await load_market_context(session, target)
    service = AnalysisService()
    av_at = availability_for(target)
    rows = [
        _snapshot_row(
            service.analyze(fd, market=market, name=name), target, av_at,
            _feature_payload(fd),
        )
        for fd, name in pairs
    ]
    n = await upsert_many(session, SignalSnapshot, rows, ["symbol", "data_date"])
    await session.commit()
    return n
