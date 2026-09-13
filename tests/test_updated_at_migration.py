"""docs/13 Phase 2：updated_at 的 DB（migration）與 ORM nullable 語意一致。

conftest 的 db_session 用 Base.metadata.create_all 建表，會直接依 ORM 生成 NOT NULL，
無法證明 migration 正確；故本檔以子行程跑真正的 `alembic upgrade head` 建 schema 再檢查。
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.db.models.market import DailyPrice
from app.repositories.upsert import upsert_many

ROOT = Path(__file__).resolve().parents[1]
TABLES = ("signal_snapshot", "daily_price", "corporate_action")
PREV_REVISION = "a7d0e4f2b6c8"
DAY = dt.date(2026, 1, 5)
AVAILABLE = "2026-01-05 18:00:00+08:00"


def _alembic(*args: str) -> None:
    env = {**os.environ, "APP_ENV": "test"}
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT, env=env, check=True, capture_output=True,
    )


def _reset_schema(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def migrated_engine():
    engine = create_engine(get_settings().active_database_url)
    _reset_schema(engine)
    _alembic("upgrade", "head")
    try:
        yield engine
    finally:
        _reset_schema(engine)
        engine.dispose()


def _seed_price(conn, **extra) -> None:
    conn.execute(text(
        "INSERT INTO stock (symbol, name, market) VALUES ('AAA', 'A', 'TWSE')"
        " ON CONFLICT DO NOTHING"
    ))
    cols = "symbol, data_date, available_at, open, high, low, close, volume, turnover"
    vals = ":s, :d, :av, 100, 100, 100, 100, 1000, 100000"
    params = {"s": "AAA", "d": DAY, "av": dt.datetime.fromisoformat(AVAILABLE)}
    if "updated_at" in extra:
        cols += ", updated_at"
        vals += ", :u"
        params["u"] = extra["updated_at"]
    conn.execute(text(f"INSERT INTO daily_price ({cols}) VALUES ({vals})"), params)


def test_migrated_updated_at_is_not_nullable(migrated_engine):
    insp = inspect(migrated_engine)
    for t in TABLES:
        col = {c["name"]: c for c in insp.get_columns(t)}["updated_at"]
        assert col["nullable"] is False, t
        assert f"ix_{t}_updated_at" in {i["name"] for i in insp.get_indexes(t)}


def test_explicit_null_updated_at_rejected(migrated_engine):
    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as conn:
            _seed_price(conn, updated_at=None)


def test_insert_without_updated_at_uses_server_default(migrated_engine):
    with migrated_engine.begin() as conn:
        _seed_price(conn)
        got = conn.execute(text("SELECT updated_at FROM daily_price")).scalar_one()
    assert got is not None


async def test_upsert_conflict_still_bumps_updated_at(migrated_engine):
    old = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    with migrated_engine.begin() as conn:
        _seed_price(conn, updated_at=old)

    aengine = create_async_engine(get_settings().active_database_url)
    try:
        async with AsyncSession(aengine) as session:
            await upsert_many(session, DailyPrice, [{
                "symbol": "AAA", "data_date": DAY,
                "available_at": dt.datetime.fromisoformat(AVAILABLE),
                "open": 101, "high": 101, "low": 101, "close": 101,
                "volume": 1000, "turnover": 1e5,
            }], ["symbol", "data_date"])
            await session.commit()
    finally:
        await aengine.dispose()

    with migrated_engine.connect() as conn:
        got = conn.execute(text("SELECT updated_at FROM daily_price")).scalar_one()
    assert got > old


def test_downgrade_upgrade_backfills_nulls_and_keeps_data(migrated_engine):
    _alembic("downgrade", PREV_REVISION)
    with migrated_engine.begin() as conn:
        _seed_price(conn)
        # 舊 schema 允許 NULL：模擬修正前被寫入的 NULL
        conn.execute(text("UPDATE daily_price SET updated_at = NULL"))
    _alembic("upgrade", "head")

    with migrated_engine.connect() as conn:
        for t in TABLES:
            nulls = conn.execute(
                text(f"SELECT count(*) FROM {t} WHERE updated_at IS NULL")
            ).scalar_one()
            assert nulls == 0, t
        assert conn.execute(text("SELECT count(*) FROM daily_price")).scalar_one() == 1
    insp = inspect(migrated_engine)
    for t in TABLES:
        assert f"ix_{t}_updated_at" in {i["name"] for i in insp.get_indexes(t)}
