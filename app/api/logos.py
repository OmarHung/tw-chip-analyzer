"""個股商標代理：`GET /api/stocks/{symbol}/logo`（熱力圖用）。

不讓瀏覽器直連 favicon 服務的原因：查無 favicon 時上游回「404 + 預設地球圖」，
瀏覽器照樣觸發 onLoad，前端分不出查無與「真的只有 16px」。這裡看狀態碼，查無一律
404，前端 onError 退回首字徽章。

快取在行程記憶體：命中與「確定查無」都快取（TTL 分開設），暫時性網路錯誤不快取，
以免一次逾時讓商標消失一整天。容量有上限，超過時丟最舊的。
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.favicon import fetch_favicon
from app.core.config import get_thresholds
from app.core.logging import get_logger
from app.db.models.market import Stock
from app.db.session import get_session

logger = get_logger(__name__)
router = APIRouter(prefix="/api/stocks", tags=["stocks"])

_SECONDS_PER_HOUR = 3600
# host → (到期時間 epoch 秒, (位元組, content-type) 或 None＝確定查無)
_cache: dict[str, tuple[float, tuple[bytes, str] | None]] = {}


def _conf(key: str, default):
    return get_thresholds().get("heatmap", "logo", key, default=default)


def clear_cache() -> None:
    _cache.clear()


def _remember(host: str, value: tuple[bytes, str] | None, ttl_hours: float) -> None:
    max_entries = int(_conf("max_cache_entries", 4000))
    while len(_cache) >= max_entries:
        _cache.pop(next(iter(_cache)))  # dict 保序：最早寫入者先出
    _cache[host] = (time.time() + ttl_hours * _SECONDS_PER_HOUR, value)


async def _favicon_for(host: str) -> tuple[bytes, str] | None:
    cached = _cache.get(host)
    if cached and cached[0] > time.time():
        return cached[1]
    try:
        value = await fetch_favicon(
            host, int(_conf("size_px", 64)), float(_conf("timeout_sec", 5))
        )
    except httpx.HTTPError as e:
        logger.warning("favicon 取得失敗 host=%s: %s", host, e)
        return None  # 暫時性錯誤：不快取
    ttl_key, ttl_default = ("cache_ttl_hours", 24) if value else ("miss_ttl_hours", 6)
    _remember(host, value, float(_conf(ttl_key, ttl_default)))
    return value


@router.get("/{symbol}/logo", response_class=Response)
async def stock_logo(symbol: str, session: AsyncSession = Depends(get_session)) -> Response:
    website = (
        await session.execute(select(Stock.website).where(Stock.symbol == symbol))
    ).scalar_one_or_none()
    host = urlsplit(website).hostname if website else None
    found = await _favicon_for(host) if host else None
    if found is None:
        raise HTTPException(status_code=404, detail="查無商標")
    content, content_type = found
    max_age = int(float(_conf("cache_ttl_hours", 24)) * _SECONDS_PER_HOUR)
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": f"public, max-age={max_age}"},
    )
