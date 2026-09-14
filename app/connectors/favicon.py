"""公司網域 favicon（熱力圖商標用；台股沒有免費的正式 logo 來源）。

Google s2 favicon 服務：查無時回 404（body 仍是一張 16px 預設地球），故一律以
狀態碼判斷，不看內容。只給 `stock.website` 正規化後的 hostname，不接受任意 URL。
"""
from __future__ import annotations

import httpx

BASE = "https://www.google.com/s2/favicons"
_HEADERS = {"User-Agent": "Mozilla/5.0 (tw-chip-analyzer)"}


async def fetch_favicon(host: str, size: int, timeout: float) -> tuple[bytes, str] | None:
    """回 (影像位元組, content-type)；上游查無 favicon 回 None，網路/5xx 錯誤直接拋出。"""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(BASE, params={"domain": host, "sz": size}, headers=_HEADERS)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    content_type = r.headers.get("content-type", "").split(";")[0].strip()
    if not content_type.startswith("image/") or not r.content:
        return None
    return r.content, content_type
