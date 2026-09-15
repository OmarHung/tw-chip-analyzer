"""Telegram Bot API 連接器（sendMessage + getUpdates 偵測 chat id）。

- 機密：bot token / chat id 由呼叫端從 Settings 帶入，本模組不讀環境。
- **token 絕不進 log**：Bot API 的 URL 內含 token，httpx 例外訊息會把 URL 印出來，
  故所有 httpx 例外一律轉成 TelegramError，訊息只留類型與去識別後的描述。
- 暫時性錯誤（逾時 / 連線重置 / 5xx / 429）依 config 重試；4xx（token 錯、chat 不存在、
  訊息格式錯）不重試，直接拋出讓呼叫端記錄。
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

API_BASE = "https://api.telegram.org"
MESSAGE_MAX_CHARS = 4096  # Telegram sendMessage 硬上限（呼叫端應在此之下分段）


_TOKEN_IN_URL = re.compile(r"/bot[^/\s]+/")


class _RedactBotToken(logging.Filter):
    """httpx 以 INFO 記錄完整請求 URL（含 /bot<token>/），在 logger 層遮蔽。"""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "/bot" in message:
            record.msg, record.args = _TOKEN_IN_URL.sub("/bot***/", message), ()
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(f, _RedactBotToken) for f in _httpx_logger.filters):
    _httpx_logger.addFilter(_RedactBotToken())


class TelegramError(RuntimeError):
    """Telegram 送出失敗（已去除 token）。"""


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str = "",
        *,
        timeout: float = 15.0,
        retries: int = 2,
        backoff_sec: float = 2.0,
        base_url: str = API_BASE,
        transport: httpx.AsyncBaseTransport | None = None,  # 測試注入 MockTransport
    ):
        if not token:
            raise ValueError("Telegram token 為必填")
        self._token = token
        self._chat_id = chat_id
        self._timeout = timeout
        self._retries = max(0, retries)
        self._backoff = backoff_sec
        self._transport = transport
        self._base_url = base_url

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "***")

    async def send_message(self, text: str, *, parse_mode: str = "HTML") -> dict:
        """送一則訊息；回傳 Telegram 的 result（含 message_id）。"""
        if not self._chat_id:
            raise ValueError("send_message 需要 chat_id")
        if len(text) > MESSAGE_MAX_CHARS:
            raise ValueError(f"訊息長度 {len(text)} 超過 Telegram 上限 {MESSAGE_MAX_CHARS}")
        return await self._call("sendMessage", {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        })

    async def get_updates(self) -> list[dict]:
        """最近收到的更新（供 UI 偵測 chat id）。bot 設了 webhook 時 Telegram 回 409。"""
        result = await self._call("getUpdates", {"timeout": 0, "allowed_updates": [
            "message", "channel_post", "my_chat_member",
        ]})
        return result if isinstance(result, list) else []

    async def _call(self, method: str, payload: dict):
        url = f"{self._base_url}/bot{self._token}/{method}"
        attempt = 0
        while True:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                    resp = await client.post(url, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt > self._retries:
                    raise TelegramError(f"{type(e).__name__}: {self._redact(str(e))}") from None
                await asyncio.sleep(self._backoff * attempt)
                continue
            if resp.status_code >= 500 or resp.status_code == 429:
                if attempt > self._retries:
                    raise TelegramError(f"HTTP {resp.status_code}: {self._describe(resp)}")
                await asyncio.sleep(self._backoff * attempt)
                continue
            if resp.status_code >= 400:
                # token / chat_id / 訊息格式問題：重試無意義
                raise TelegramError(f"HTTP {resp.status_code}: {self._describe(resp)}")
            data = resp.json()
            if not data.get("ok"):
                raise TelegramError(self._redact(str(data.get("description", "unknown"))))
            return data.get("result", {})

    def _describe(self, resp: httpx.Response) -> str:
        try:
            desc = resp.json().get("description", "")
        except ValueError:
            desc = resp.text[:200]
        return self._redact(str(desc))
