"""門檻設定改存 DB（YAML 為預設、DB 只存覆寫值）。

- 只有登錄表（threshold_registry）列出的鍵可改，型別/範圍/選項驗證。
- 未經 OOS 驗證而刻意為 0 的權重預設鎖定，需明確解鎖。
- 每次修改寫歷史（舊值/新值/來源）。
- data_version：只涵蓋「影響已落地資料」的設定；改了它 → 歷史分數需重建。
"""
from __future__ import annotations

import copy
import datetime as dt

import httpx
import pytest
from httpx import ASGITransport

from app.core import config as config_mod
from app.core import threshold_registry as reg
from app.db.models.features import SignalSnapshot
from app.db.models.market import Stock
from app.db.models.settings import ThresholdChange, ThresholdOverride
from app.importers.base import availability_for
from app.services import threshold_settings as svc

BASE = {
    "signal": {"buy_score": 75, "watch_score": 65},
    "weights": {
        "composite": {"intraday": 0.35, "institutional": 0.30},
        "institutional": {"sbl_change": 0.0, "trust": 0.30},
    },
    "scoring": {"mapping": "percentile"},
}


class TestPureHelpers:
    def test_apply_overrides_is_immutable_and_nested(self):
        base = copy.deepcopy(BASE)
        out = reg.apply_overrides(base, {"signal.buy_score": 80})
        assert out["signal"]["buy_score"] == 80
        assert base["signal"]["buy_score"] == 75  # 原 dict 不被改

    def test_unknown_override_keys_are_ignored(self):
        out = reg.apply_overrides(BASE, {"weights.intraday.removed_factor": 0.2})
        assert out == BASE

    def test_validate_type_and_range(self):
        assert reg.validate("signal.buy_score", "80") == 80.0
        with pytest.raises(ValueError):
            reg.validate("signal.buy_score", 150)
        with pytest.raises(ValueError):
            reg.validate("scoring.mapping", "bogus")
        with pytest.raises(KeyError):
            reg.validate("database_url", "x")

    def test_locked_weight_requires_unlock(self):
        with pytest.raises(PermissionError):
            reg.validate("weights.institutional.sbl_change", -0.05)
        assert reg.validate("weights.institutional.sbl_change", -0.05, unlock=True) == -0.05

    def test_data_version_tracks_only_rebuild_settings(self):
        v0 = reg.data_version(BASE)
        live = reg.apply_overrides(BASE, {"signal.buy_score": 80})
        rebuild = reg.apply_overrides(BASE, {"weights.composite.intraday": 0.4})
        assert reg.data_version(live) == v0
        assert reg.data_version(rebuild) != v0

    def test_registry_keys_exist_in_yaml(self):
        """登錄表的每個鍵都必須對應 thresholds.yaml 真實存在的設定（防改名後失聯）。"""
        yaml_data = config_mod.load_yaml_thresholds()
        missing = [k for k in reg.REGISTRY if reg.get_path(yaml_data, k) is None]
        assert missing == []


def test_get_thresholds_merges_db_overrides(monkeypatch):
    monkeypatch.setattr(config_mod, "_load_overrides_sync", lambda: {"signal.buy_score": 81})
    monkeypatch.setattr(config_mod, "_overrides_enabled", lambda: True)
    config_mod.reload_thresholds()
    try:
        assert config_mod.get_thresholds().signal["buy_score"] == 81
    finally:
        monkeypatch.undo()
        config_mod.reload_thresholds()


async def test_set_and_reset_setting_writes_history(db_session):
    await svc.set_setting(db_session, "signal.buy_score", 80, source="test")
    assert (await svc.load_overrides(db_session)) == {"signal.buy_score": 80.0}

    await svc.set_setting(db_session, "signal.buy_score", 78, source="test")
    await svc.reset_setting(db_session, "signal.buy_score", source="test")
    assert await svc.load_overrides(db_session) == {}

    rows = (await db_session.execute(
        ThresholdChange.__table__.select().order_by(ThresholdChange.id)
    )).all()
    assert [(r.old_value, r.new_value) for r in rows] == [(None, 80.0), (80.0, 78.0), (78.0, None)]
    assert all(r.source == "test" for r in rows)


async def test_list_settings_shows_default_override_and_orphans(db_session):
    await svc.set_setting(db_session, "signal.buy_score", 80, source="test")
    db_session.add(ThresholdOverride(key="weights.intraday.removed_factor", value=0.2))  # 已不存在的鍵
    await db_session.commit()

    out = await svc.list_settings(db_session)
    item = next(i for i in out["items"] if i["key"] == "signal.buy_score")
    assert item["default"] == config_mod.load_yaml_thresholds()["signal"]["buy_score"]
    assert item["override"] == 80.0 and item["effective"] == 80.0
    assert item["effect"] == "live"
    assert out["orphaned"] == ["weights.intraday.removed_factor"]


async def test_pending_rebuild_detects_version_mismatch(db_session):
    db_session.add(Stock(symbol="AAA", name="A", market="TWSE"))
    await db_session.flush()
    current = reg.data_version(config_mod.get_thresholds().raw)
    d = dt.date(2026, 9, 8)
    db_session.add(SignalSnapshot(symbol="AAA", data_date=d, available_at=availability_for(d),
                                  chip_score=60, action="HOLD", config_version=current))
    await db_session.commit()
    assert (await svc.pending_rebuild(db_session, current)) is False
    assert (await svc.pending_rebuild(db_session, "other-version")) is True


async def test_persist_signals_records_config_version(db_session):
    from app.db.models.features import FeatureDaily
    from app.services.signal_persist import persist_signals

    db_session.add(Stock(symbol="AAA", name="A", market="TWSE"))
    await db_session.flush()
    d = dt.date(2026, 9, 8)
    db_session.add(FeatureDaily(symbol="AAA", data_date=d, available_at=availability_for(d),
                                close=100, atr14=2, turnover=1e9, foreign_5d_z=1.0))
    await db_session.commit()
    await persist_signals(db_session, d)
    snap = (await db_session.execute(SignalSnapshot.__table__.select())).one()
    assert snap.config_version == reg.data_version(config_mod.get_thresholds().raw)


@pytest.fixture
async def client(db_session):
    from app.db.session import get_session
    from app.main import app

    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    config_mod.reload_thresholds()


async def test_api_read_update_reset(client, monkeypatch):
    monkeypatch.setattr(config_mod.get_settings(), "ops_api_key", "")
    r = await client.get("/api/ops/settings")
    assert r.status_code == 200 and r.json()["pending_rebuild"] is False

    r = await client.put("/api/ops/settings/signal.buy_score", json={"value": 82})
    assert r.status_code == 200
    item = next(i for i in r.json()["items"] if i["key"] == "signal.buy_score")
    assert item["effective"] == 82.0

    assert (await client.put("/api/ops/settings/signal.buy_score", json={"value": 999})).status_code == 422
    assert (await client.put("/api/ops/settings/nope.key", json={"value": 1})).status_code == 404
    locked = await client.put("/api/ops/settings/weights.institutional.sbl_change", json={"value": -0.1})
    assert locked.status_code == 409

    assert (await client.delete("/api/ops/settings/signal.buy_score")).status_code == 200


async def test_api_update_requires_ops_auth(client, monkeypatch):
    monkeypatch.setattr(config_mod.get_settings(), "ops_api_key", "k")
    r = await client.put("/api/ops/settings/signal.buy_score", json={"value": 80})
    assert r.status_code == 401
    assert (await client.get("/api/ops/settings")).status_code == 200  # 讀取免金鑰
