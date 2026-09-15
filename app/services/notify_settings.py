"""Telegram 推播設定：UI（DB）> .env > YAML 三層合併、驗證、測試訊息、chat id 偵測。

- DB `notify_setting` 欄位 NULL＝沿用下一層；UI「清除」即寫回 NULL。
- bot_token 是機密：對外一律遮蔽（只露 bot id 與末 4 碼），不進 log 與修改紀錄。
- 格式參數（lookback_days、分段長度、逾時/重試）只在 YAML，UI 不開放。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.telegram import TelegramClient
from app.core.config import get_settings, get_thresholds
from app.db.models.settings import NotifySetting
from app.models.signal import Action

CHANNEL = "telegram"
ALLOWED_ACTIONS: tuple[str, ...] = tuple(a.value for a in Action)

_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
_CHAT_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,})$")
_SECRET_FIELDS = ("bot_token", "chat_id")  # 預設來自 .env，其餘來自 YAML
_INT_FIELDS: dict[str, tuple[int, int]] = {"max_items": (1, 200), "max_reasons": (0, 10)}
_EDITABLE = ("enabled", "bot_token", "chat_id", "actions", "max_items", "max_reasons",
             "min_turnover")


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool
    bot_token: str
    chat_id: str
    actions: tuple[str, ...]
    max_items: int
    max_reasons: int
    min_turnover: float
    lookback_days: int
    max_message_chars: int
    timeout_sec: float
    retries: int
    backoff_sec: float

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def client(self, **kw: Any) -> TelegramClient:
        return TelegramClient(self.bot_token, self.chat_id, timeout=self.timeout_sec,
                              retries=self.retries, backoff_sec=self.backoff_sec, **kw)


def _yaml() -> dict:
    return get_thresholds().get("notify", "telegram", default={}) or {}


def _defaults() -> dict[str, Any]:
    """DB 未設定時的值（env 優先於 YAML 的只有機密兩欄）。"""
    conf, env = _yaml(), get_settings()
    return {
        "enabled": bool(conf.get("enabled", False)),
        "bot_token": env.telegram_bot_token or "",
        "chat_id": env.telegram_chat_id or "",
        "actions": [str(a) for a in (conf.get("actions") or ["BUY", "AVOID"])],
        "max_items": int(conf.get("max_items", 20)),
        "max_reasons": int(conf.get("max_reasons", 2)),
        "min_turnover": float(conf.get("min_turnover", 0) or 0),
    }


async def _row(session: AsyncSession) -> NotifySetting | None:
    return await session.get(NotifySetting, CHANNEL)


def _merged(row: NotifySetting | None) -> tuple[dict[str, Any], dict[str, str]]:
    defaults = _defaults()
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for f in _EDITABLE:
        db_value = getattr(row, f) if row is not None else None
        if db_value is not None:
            values[f], sources[f] = db_value, "db"
        else:
            values[f] = defaults[f]
            if f in _SECRET_FIELDS:
                sources[f] = "env" if defaults[f] else "unset"
            else:
                sources[f] = "yaml"
    return values, sources


async def resolve_telegram(session: AsyncSession) -> TelegramConfig:
    values, _ = _merged(await _row(session))
    conf = _yaml()
    return TelegramConfig(
        enabled=bool(values["enabled"]),
        bot_token=str(values["bot_token"] or ""),
        chat_id=str(values["chat_id"] or ""),
        actions=tuple(values["actions"]),
        max_items=int(values["max_items"]),
        max_reasons=int(values["max_reasons"]),
        min_turnover=float(values["min_turnover"]),
        lookback_days=int(conf.get("lookback_days", 10)),
        max_message_chars=int(conf.get("max_message_chars", 3900)),
        timeout_sec=float(conf.get("timeout_sec", 15)),
        retries=int(conf.get("retries", 2)),
        backoff_sec=float(conf.get("backoff_sec", 2.0)),
    )


def mask_token(token: str) -> str | None:
    if not token:
        return None
    bot_id, _, secret = token.partition(":")
    return f"{bot_id}:…{secret[-4:]}" if secret else "…"


async def telegram_view(session: AsyncSession) -> dict:
    row = await _row(session)
    values, sources = _merged(row)
    defaults = _defaults()
    token = str(values.pop("bot_token") or "")
    return {
        **values,
        "token_set": bool(token),
        "token_masked": mask_token(token),
        "sources": sources,
        "defaults": {k: v for k, v in defaults.items() if k != "bot_token"}
        | {"bot_token_masked": mask_token(defaults["bot_token"])},
        "allowed_actions": list(ALLOWED_ACTIONS),
        "updated_by": row.updated_by if row else None,
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }


def validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """驗證 UI 送來的部分更新；值為 None＝清除 DB 值（回到 .env / YAML）。"""
    unknown = set(patch) - set(_EDITABLE)
    if unknown:
        raise ValueError(f"不支援的欄位：{', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for key, value in patch.items():
        if value is None:
            out[key] = None
        elif key == "enabled":
            if not isinstance(value, bool):
                raise ValueError("enabled 必須為 true/false")
            out[key] = value
        elif key == "bot_token":
            token = str(value).strip()
            if not _TOKEN_RE.match(token):
                raise ValueError("Bot token 格式錯誤（應為 123456789:AA… ，向 @BotFather 取得）")
            out[key] = token
        elif key == "chat_id":
            chat = str(value).strip()
            if not _CHAT_RE.match(chat):
                raise ValueError("Chat ID 格式錯誤（數字，群組/頻道為負數；或 @頻道名稱）")
            out[key] = chat
        elif key == "actions":
            if not isinstance(value, list) or not value:
                raise ValueError("推播類型至少選一個")
            bad = [a for a in value if a not in ALLOWED_ACTIONS]
            if bad:
                raise ValueError(f"不支援的推播類型：{', '.join(map(str, bad))}")
            out[key] = [a for a in ALLOWED_ACTIONS if a in value]  # 去重並固定順序
        elif key in _INT_FIELDS:
            lo, hi = _INT_FIELDS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or float(value) != int(value) or not lo <= int(value) <= hi:
                raise ValueError(f"{key} 必須為 {lo}～{hi} 的整數")
            out[key] = int(value)
        elif key == "min_turnover":
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not 0 <= float(value) <= 1e12:
                raise ValueError("min_turnover 必須為 0～1e12")
            out[key] = float(value)
    return out


async def update_telegram(session: AsyncSession, patch: dict[str, Any], *, source: str) -> list[str]:
    """寫入部分更新，回傳異動欄位名（供稽核 log；不含值，避免 token 外洩）。"""
    clean = validate_patch(patch)
    row = await _row(session)
    if row is None:
        row = NotifySetting(channel=CHANNEL)
        session.add(row)
    for key, value in clean.items():
        setattr(row, key, value)
    row.updated_by = source
    await session.commit()
    await session.refresh(row)  # updated_at 由 DB 產生；async 下不可 lazy load
    return sorted(clean)


def _chat_title(chat: dict) -> str:
    if chat.get("title"):
        return str(chat["title"])
    name = " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if p)
    if chat.get("username"):
        name = f"{name} (@{chat['username']})".strip()
    return name or str(chat.get("id"))


def extract_chats(updates: list[dict]) -> list[dict]:
    """getUpdates → 不重複的 chat 清單（最新出現的在前）。"""
    seen: dict[int, dict] = {}
    for upd in reversed(updates):
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (upd.get(key) or {}).get("chat")
            if chat and chat.get("id") is not None and chat["id"] not in seen:
                seen[chat["id"]] = {
                    "chat_id": str(chat["id"]),
                    "type": chat.get("type", ""),
                    "title": _chat_title(chat),
                }
    return list(seen.values())


async def send_test_message(config: TelegramConfig) -> dict:
    """用目前生效設定送一則測試訊息；未設定拋 ValueError，送出失敗拋 TelegramError。"""
    if not config.configured:
        raise ValueError("尚未設定 Bot token 與 Chat ID")
    result = await config.client().send_message(
        "✅ <b>tw_chip_analyzer</b> 測試訊息\n"
        f"推播類型：{'、'.join(config.actions)}"
        f"（{'已啟用' if config.enabled else '未啟用，EOD 不會自動推播'}）"
    )
    return {"message_id": result.get("message_id")}


async def detect_chats(config: TelegramConfig, token: str | None = None) -> list[dict]:
    """列出最近傳訊給 bot 的 chat。token 省略＝用已儲存的；需先對 bot 傳過訊息。"""
    use = validate_patch({"bot_token": token})["bot_token"] if token else config.bot_token
    if not use:
        raise ValueError("請先輸入 Bot token")
    client = TelegramClient(use, timeout=config.timeout_sec, retries=config.retries,
                            backoff_sec=config.backoff_sec)
    return extract_chats(await client.get_updates())
