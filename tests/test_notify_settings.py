"""Telegram 推播 UI 設定：DB > .env > YAML 合併、驗證、token 遮蔽、API 認證與測試/偵測端點。"""
from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from app.connectors.telegram import TelegramError
from app.core.config import get_settings
from app.db.models.settings import NotifySetting
from app.services import notify_settings as svc

TOKEN = "123456789:AAH" + "k" * 32 + "WXYZ"
ENV_TOKEN = "987654321:BBB" + "e" * 32 + "ENVV"


@pytest.fixture
def no_env_secrets(monkeypatch):
    """隔離本機 .env：預設無 token / chat id。"""
    monkeypatch.setattr(get_settings(), "telegram_bot_token", "")
    monkeypatch.setattr(get_settings(), "telegram_chat_id", "")


# ---------------------------------------------------------------- 驗證

def test_validate_patch_accepts_and_normalises():
    out = svc.validate_patch({
        "enabled": True, "bot_token": f"  {TOKEN} ", "chat_id": "-1001234567890",
        "actions": ["AVOID", "BUY", "BUY"], "max_items": 30.0, "min_turnover": 5e7,
    })
    assert out["bot_token"] == TOKEN
    assert out["actions"] == ["BUY", "AVOID"]  # 去重、固定順序
    assert out["max_items"] == 30 and isinstance(out["max_items"], int)
    assert svc.validate_patch({"chat_id": "@my_channel"})["chat_id"] == "@my_channel"
    assert svc.validate_patch({"bot_token": None}) == {"bot_token": None}  # 清除


@pytest.mark.parametrize("patch", [
    {"bot_token": "not-a-token"},
    {"chat_id": "abc"},
    {"actions": []},
    {"actions": ["BUY", "SELL"]},
    {"max_items": 0},
    {"max_reasons": 1.5},
    {"enabled": "yes"},
    {"min_turnover": -1},
    {"lookback_days": 3},  # YAML-only，UI 不可改
])
def test_validate_patch_rejects(patch):
    with pytest.raises(ValueError):
        svc.validate_patch(patch)


def test_mask_token_never_reveals_secret():
    masked = svc.mask_token(TOKEN)
    assert masked == "123456789:…WXYZ"
    assert "k" * 8 not in masked
    assert svc.mask_token("") is None


def test_extract_chats_dedupes_latest_first():
    updates = [
        {"message": {"chat": {"id": 42, "type": "private", "first_name": "Omar",
                              "username": "omar"}}},
        {"channel_post": {"chat": {"id": -100123, "type": "channel", "title": "籌碼"}}},
        {"message": {"chat": {"id": 42, "type": "private", "first_name": "Omar"}}},
    ]
    chats = svc.extract_chats(updates)
    assert [c["chat_id"] for c in chats] == ["42", "-100123"]
    assert chats[1]["title"] == "籌碼"


# ---------------------------------------------------------------- 合併優先序

async def test_resolve_falls_back_to_env_then_db_wins(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_bot_token", ENV_TOKEN)
    monkeypatch.setattr(get_settings(), "telegram_chat_id", "111")

    conf = await svc.resolve_telegram(db_session)
    assert conf.bot_token == ENV_TOKEN and conf.chat_id == "111"
    view = await svc.telegram_view(db_session)
    assert view["sources"]["bot_token"] == "env" and view["sources"]["actions"] == "yaml"

    await svc.update_telegram(db_session, {"bot_token": TOKEN, "chat_id": "222",
                                           "actions": ["BUY"]}, source="test")
    conf = await svc.resolve_telegram(db_session)
    assert (conf.bot_token, conf.chat_id, conf.actions) == (TOKEN, "222", ("BUY",))

    await svc.update_telegram(db_session, {"bot_token": None}, source="test")
    conf = await svc.resolve_telegram(db_session)
    assert conf.bot_token == ENV_TOKEN and conf.chat_id == "222"  # 只清 token


async def test_view_never_contains_raw_token(db_session, no_env_secrets):
    await svc.update_telegram(db_session, {"bot_token": TOKEN}, source="test")
    view = await svc.telegram_view(db_session)
    assert TOKEN not in repr(view)
    assert view["token_set"] is True and view["token_masked"] == "123456789:…WXYZ"
    assert view["sources"]["chat_id"] == "unset"
    assert view["updated_by"] == "test" and view["updated_at"]


# ---------------------------------------------------------------- API

@pytest.fixture
async def client(db_session, no_env_secrets):
    from app.db.session import get_session
    from app.main import app

    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app, client=("127.0.0.1", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_api_get_put_masks_token(client):
    r = await client.get("/api/ops/notify/telegram")
    assert r.status_code == 200 and r.json()["token_set"] is False

    r = await client.put("/api/ops/notify/telegram",
                         json={"enabled": True, "bot_token": TOKEN, "chat_id": "42"})
    assert r.status_code == 200
    assert TOKEN not in r.text
    body = r.json()
    assert body["enabled"] is True and body["chat_id"] == "42" and body["token_set"] is True

    assert (await client.put("/api/ops/notify/telegram",
                             json={"chat_id": "bad id"})).status_code == 422


async def test_api_writes_require_ops_key(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "ops_api_key", "secret-key")
    assert (await client.put("/api/ops/notify/telegram", json={"enabled": True})).status_code == 401
    assert (await client.post("/api/ops/notify/telegram/test")).status_code == 401
    assert (await client.post("/api/ops/notify/telegram/detect-chats", json={})).status_code == 401
    ok = await client.put("/api/ops/notify/telegram", json={"enabled": True},
                          headers={"X-Ops-Key": "secret-key"})
    assert ok.status_code == 200


async def test_api_test_message(client, monkeypatch):
    assert (await client.post("/api/ops/notify/telegram/test")).status_code == 400  # 未設定

    await client.put("/api/ops/notify/telegram", json={"bot_token": TOKEN, "chat_id": "42"})
    sent = []

    class Fake:
        async def send_message(self, text):
            sent.append(text)
            return {"message_id": 9}

    monkeypatch.setattr(svc.TelegramConfig, "client", lambda self: Fake())
    r = await client.post("/api/ops/notify/telegram/test")
    assert r.status_code == 200 and r.json() == {"message_id": 9}
    assert "測試訊息" in sent[0]

    class Broken:
        async def send_message(self, text):
            raise TelegramError("HTTP 400: chat not found")

    monkeypatch.setattr(svc.TelegramConfig, "client", lambda self: Broken())
    r = await client.post("/api/ops/notify/telegram/test")
    assert r.status_code == 502 and "chat not found" in r.json()["detail"]


async def test_api_detect_chats_uses_draft_token(client, monkeypatch):
    seen = []

    async def fake_updates(self):
        seen.append(self._token)
        return [{"message": {"chat": {"id": 42, "type": "private", "first_name": "O"}}}]

    monkeypatch.setattr(svc.TelegramClient, "get_updates", fake_updates)
    assert (await client.post("/api/ops/notify/telegram/detect-chats", json={})).status_code == 400
    r = await client.post("/api/ops/notify/telegram/detect-chats", json={"bot_token": TOKEN})
    assert r.status_code == 200 and r.json()["chats"][0]["chat_id"] == "42"
    assert seen == [TOKEN]
    bad = await client.post("/api/ops/notify/telegram/detect-chats", json={"bot_token": "x"})
    assert bad.status_code == 400
