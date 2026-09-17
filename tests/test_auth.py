"""全站登入認證與角色權限（docs/17）。

覆蓋三條通過路徑與其邊界：
- session cookie（登入/登出/過期/撤銷/角色）
- X-Ops-Key（非瀏覽器用戶端，等同 admin）
- 相容模式（尚未建立任何帳號時行為與加認證前相同）
"""
from __future__ import annotations

import datetime as dt

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password, token_fingerprint, verify_password
from app.db.models.auth import UserSession
from app.db.session import get_session
from app.main import app
from app.services import auth as svc

READ_PATH = "/api/ops/tasks"      # 唯讀端點（不需任何資料即可回 200）
WRITE_PATH = "/api/ops/settings/signal.buy_score"  # 寫入端點（admin）
PASSWORD = "correct-horse-battery"


@pytest_asyncio.fixture
async def client(db_session, monkeypatch):
    svc.reset_throttle()
    svc.invalidate_auth_cache()
    monkeypatch.setattr(get_settings(), "ops_api_key", "")

    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin(db_session):
    return await svc.create_user(db_session, "boss", PASSWORD, "admin")


@pytest_asyncio.fixture
async def viewer(db_session):
    return await svc.create_user(db_session, "guest", PASSWORD, "viewer")


async def _login(c: httpx.AsyncClient, username: str, password: str = PASSWORD):
    return await c.post("/api/auth/login", json={"username": username, "password": password})


# --- 密碼雜湊 ---


def test_password_hash_roundtrip():
    encoded = hash_password("s3cret-password")
    assert "s3cret-password" not in encoded          # 不可還原
    assert verify_password("s3cret-password", encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password("s3cret-password", "not-a-hash")


def test_hash_is_salted():
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


# --- 相容模式（尚未建立帳號） ---


async def test_compat_mode_keeps_read_open(client):
    assert (await client.get(READ_PATH)).status_code == 200
    assert (await client.get("/api/auth/state")).json() == {
        "auth_enabled": False, "has_users": False,
    }


# --- session 認證 ---


async def test_read_requires_login_once_user_exists(client, admin):
    assert (await client.get(READ_PATH)).status_code == 401
    assert (await _login(client, "boss")).status_code == 200
    assert (await client.get(READ_PATH)).status_code == 200


async def test_login_rejects_wrong_password(client, admin):
    r = await _login(client, "boss", "wrong-password")
    assert r.status_code == 401
    assert (await client.get(READ_PATH)).status_code == 401


async def test_login_rejects_unknown_user(client, admin):
    assert (await _login(client, "nobody")).status_code == 401


async def test_me_reports_role(client, viewer):
    await _login(client, "guest")
    me = (await client.get("/api/auth/me")).json()
    assert me["authenticated"] and me["role"] == "viewer" and me["username"] == "guest"


async def test_logout_invalidates_session(client, admin):
    await _login(client, "boss")
    assert (await client.post("/api/auth/logout")).status_code == 200
    assert (await client.get(READ_PATH)).status_code == 401


async def test_session_token_stored_hashed_only(client, admin, db_session):
    await _login(client, "boss")
    token = client.cookies.get("twchip_session")
    rows = (await db_session.execute(select(UserSession))).scalars().all()
    assert len(rows) == 1
    assert rows[0].token_hash == token_fingerprint(token) != token


async def test_expired_session_rejected(client, admin, db_session):
    await _login(client, "boss")
    row = (await db_session.execute(select(UserSession))).scalars().one()
    row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    await db_session.commit()
    assert (await client.get(READ_PATH)).status_code == 401


async def test_deactivating_user_kills_session(client, admin, viewer, db_session):
    await _login(client, "guest")
    assert (await client.get(READ_PATH)).status_code == 200
    await svc.update_user(db_session, viewer, is_active=False)
    assert (await client.get(READ_PATH)).status_code == 401


# --- 角色權限 ---


async def test_viewer_cannot_write(client, viewer):
    await _login(client, "guest")
    r = await client.put(WRITE_PATH, json={"value": 76, "unlock": False})
    assert r.status_code == 403


async def test_admin_can_write(client, admin):
    await _login(client, "boss")
    r = await client.put(WRITE_PATH, json={"value": 76, "unlock": False})
    assert r.status_code == 200


async def test_viewer_cannot_manage_users(client, viewer):
    await _login(client, "guest")
    assert (await client.get("/api/auth/users")).status_code == 403
    r = await client.post(
        "/api/auth/users", json={"username": "mallory", "password": PASSWORD, "role": "admin"}
    )
    assert r.status_code == 403


# --- X-Ops-Key 並行路徑 ---


async def test_ops_key_still_works_when_users_exist(client, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "ops_api_key", "k3y-for-scripts")
    assert (await client.get(READ_PATH, headers={"X-Ops-Key": "k3y-for-scripts"})).status_code == 200
    r = await client.put(
        WRITE_PATH, json={"value": 76, "unlock": False}, headers={"X-Ops-Key": "k3y-for-scripts"}
    )
    assert r.status_code == 200
    assert (await client.get(READ_PATH, headers={"X-Ops-Key": "wrong"})).status_code == 401


# --- 限流 ---


async def test_lockout_after_repeated_failures(client, admin, monkeypatch):
    monkeypatch.setattr(svc, "conf", lambda k, d: 3 if k == "max_failed_attempts" else d)
    for _ in range(3):
        assert (await _login(client, "boss", "wrong-password")).status_code == 401
    # 鎖定後連正確密碼也擋（429），避免暴力嘗試
    assert (await _login(client, "boss")).status_code == 429


# --- 密碼變更 ---


async def test_change_password_keeps_current_session_only(client, admin, db_session):
    await _login(client, "boss")
    other = ASGITransport(app=app, client=("127.0.0.1", 50001))
    async with httpx.AsyncClient(transport=other, base_url="http://test") as c2:
        await _login(c2, "boss")
        r = await client.post(
            "/api/auth/password",
            json={"current_password": PASSWORD, "new_password": "brand-new-password"},
        )
        assert r.status_code == 200
        assert (await client.get(READ_PATH)).status_code == 200      # 本機 session 保留
        assert (await c2.get(READ_PATH)).status_code == 401          # 其他裝置被登出


async def test_change_password_requires_current(client, admin):
    await _login(client, "boss")
    r = await client.post(
        "/api/auth/password",
        json={"current_password": "wrong-password", "new_password": "brand-new-password"},
    )
    assert r.status_code == 400


async def test_short_password_rejected(client, admin):
    await _login(client, "boss")
    r = await client.post(
        "/api/auth/users", json={"username": "weak", "password": "123", "role": "viewer"}
    )
    assert r.status_code == 422


# --- 帳號管理護欄 ---


async def test_cannot_demote_last_admin(client, admin, db_session):
    await _login(client, "boss")
    r = await client.patch(f"/api/auth/users/{admin.id}", json={"role": "viewer"})
    assert r.status_code == 409
    r = await client.patch(f"/api/auth/users/{admin.id}", json={"is_active": False})
    assert r.status_code == 409


async def test_cannot_delete_self(client, admin):
    await _login(client, "boss")
    assert (await client.delete(f"/api/auth/users/{admin.id}")).status_code == 409


async def test_admin_can_create_and_reset(client, admin, db_session):
    await _login(client, "boss")
    created = (await client.post(
        "/api/auth/users",
        json={"username": "analyst", "password": PASSWORD, "role": "viewer"},
    )).json()
    assert created["role"] == "viewer"
    r = await client.post(
        f"/api/auth/users/{created['id']}/password", json={"new_password": "another-password"}
    )
    assert r.status_code == 200 and r.json()["must_change_password"] is True
