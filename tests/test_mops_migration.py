"""Phase 2 MOPS migration（c9d2e7f1a3b5 + 修正 d4f7a2c9e1b3）：以真正的 alembic upgrade/downgrade 驗證，
不用 create_all 冒充。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings
from app.db.base import Base
import app.db.models  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
PREV = "b8e1f5a3c7d9"
MOPS_BASE = "c9d2e7f1a3b5"
HEAD = "a1b2c3d4e5f6"  # app_user / user_session（全站認證，疊在 notify_setting 之上）
TABLES = ("insider_holding_monthly", "insider_transfer_declaration", "mops_fetch_coverage",
          "mops_shadow_feature_daily")
# 疊在 MOPS 之後的非 MOPS 表：降到 PREV 時一併移除，不算 MOPS migration 誤刪
LATER_TABLES = ("notify_setting", "app_user", "user_session")


def _alembic(*args: str) -> str:
    env = {**os.environ, "APP_ENV": "test"}
    out = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
                         check=True, capture_output=True, text=True)
    return out.stdout + out.stderr


def _reset(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def engine():
    eng = create_engine(get_settings().active_database_url)
    _reset(eng)
    try:
        yield eng
    finally:
        _reset(eng)
        eng.dispose()


def test_single_head():
    heads = [ln for ln in _alembic("heads").splitlines() if "(head)" in ln]
    assert len(heads) == 1 and heads[0].startswith(HEAD)


def test_upgrade_matches_orm_metadata(engine):
    _alembic("upgrade", "head")
    insp = inspect(engine)
    for t in TABLES:
        db_cols = {c["name"]: c for c in insp.get_columns(t)}
        orm_cols = Base.metadata.tables[t].columns
        assert set(db_cols) == {c.name for c in orm_cols}, t
        for c in orm_cols:
            assert db_cols[c.name]["nullable"] == c.nullable, f"{t}.{c.name}"
        if "available_at" in db_cols:
            assert db_cols["available_at"]["type"].timezone is True
            assert db_cols["available_at"]["nullable"] is False
        uniques = {u["name"]: u["column_names"] for u in insp.get_unique_constraints(t)}
        orm_uq = next(c for c in Base.metadata.tables[t].constraints if c.name == f"uq_{t}")
        assert uniques[f"uq_{t}"] == [c.name for c in orm_uq.columns], t
        for name in ("ingestion_mode", "observed_at", "scope_key", "holding_provenance",
                     "transfer_provenance"):
            if name in db_cols:  # 回填用的 server_default 已移除，與 ORM 一致
                assert db_cols[name]["default"] is None, f"{t}.{name}"
    holding_types = {c["name"]: c["type"] for c in insp.get_columns("insider_holding_monthly")}
    assert holding_types["current_shares"].python_type is int  # BIGINT，不用 float 存股數
    assert (holding_types["pledge_pct"].precision, holding_types["pledge_pct"].scale) == (7, 2)


def test_downgrade_removes_only_mops_tables_then_upgrade_again(engine):
    _alembic("upgrade", "head")
    before = set(inspect(engine).get_table_names())
    _alembic("downgrade", PREV)
    after_down = set(inspect(engine).get_table_names())
    assert before - after_down == set(TABLES) | set(LATER_TABLES)
    _alembic("upgrade", "head")
    assert set(inspect(engine).get_table_names()) == before


def _exec(engine, sql: str, **params) -> None:
    with engine.begin() as conn:
        conn.execute(text(sql), params)


def _scalar(engine, sql: str):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


def test_corrective_migration_normalizes_tpex_and_backfills_provenance(engine):
    _alembic("upgrade", MOPS_BASE)
    _exec(engine, "INSERT INTO stock (symbol, name, market) VALUES ('5386', '青雲', 'TPEx')")
    av = "2026-08-21 08:00+08"
    _exec(engine, f"""INSERT INTO insider_holding_monthly (symbol, market, source, report_date, title,
        holder_name, row_seq, current_shares, data_date, available_at)
        VALUES ('5386', 'TPEX', 'openapi', '2026-08-20', '董事本人', '柯聰源', 0, 1, '2026-07-31', '{av}')""")
    _exec(engine, f"""INSERT INTO insider_transfer_declaration (symbol, market, source, row_hash,
        reporter_role, reporter_name, method_category, data_date, available_at)
        VALUES ('5386', 'TPEX', 'mops_web', 'h', '董事本人', '柯聰源', 'market', '2026-03-10', '{av}')""")
    for mk, d in (("TPEX", "2026-03-10"), ("TPEx", "2026-03-10"), ("TPEX", "2026-03-11")):
        _exec(engine, f"""INSERT INTO mops_fetch_coverage (dataset, market, data_date, source, row_count)
            VALUES ('insider_transfer', '{mk}', '{d}', 'mops_web', 0)""")

    _alembic("upgrade", "head")
    for t in ("insider_holding_monthly", "insider_transfer_declaration", "mops_fetch_coverage"):
        assert _scalar(engine, f"SELECT count(*) FROM {t} WHERE market = 'TPEX'") == 0, t
        assert _scalar(engine, f"SELECT count(*) FROM {t} WHERE ingestion_mode <> 'unknown'") == 0, t
        assert _scalar(engine, f"SELECT count(*) FROM {t} WHERE observed_at IS NULL") == 0, t
    # 同鍵 TPEX/TPEx 衝突已安全合併，另一天的 TPEX 列正規化保留
    assert _scalar(engine, "SELECT count(*) FROM mops_fetch_coverage WHERE market = 'TPEx'") == 2
    assert _scalar(engine, "SELECT count(*) FROM mops_fetch_coverage WHERE scope_key <> '*'") == 0
    assert _scalar(engine, "SELECT market FROM stock WHERE symbol = '5386'") == "TPEx"  # Phase 1 表不動

    # 持股涵蓋列（新唯一鍵才允許）→ downgrade 會刪除，否則舊唯一鍵衝突
    for sym in ("5386", "6488"):
        _exec(engine, f"""INSERT INTO mops_fetch_coverage (dataset, market, data_date, scope_key, source,
            row_count, ingestion_mode, observed_at)
            VALUES ('insider_holding', 'TPEx', '2026-07-31', '{sym}', 'mops_web', 0, 'backfill', now())""")
    _alembic("downgrade", MOPS_BASE)
    cols = {c["name"] for c in inspect(engine).get_columns("mops_fetch_coverage")}
    assert "scope_key" not in cols and "ingestion_mode" not in cols
    assert _scalar(engine, "SELECT count(*) FROM mops_fetch_coverage") == 2
    _alembic("upgrade", "head")
    assert _scalar(engine, "SELECT count(*) FROM mops_fetch_coverage WHERE market = 'TPEX'") == 0
