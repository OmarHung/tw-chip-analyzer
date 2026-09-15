"""推播設定 API（Telegram）：讀取免金鑰（token 遮蔽）；修改/測試/偵測需 ops 認證。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ops_auth import client_desc, require_ops_auth
from app.connectors.telegram import TelegramError
from app.core.logging import get_logger
from app.db.session import get_session
from app.services import notify_settings as svc

router = APIRouter(prefix="/api/ops/notify/telegram", tags=["ops"])
audit = get_logger("api.ops.audit")


class DetectRequest(BaseModel):
    bot_token: str | None = None  # 尚未儲存時可先帶入試抓


@router.get("")
async def get_telegram(session: AsyncSession = Depends(get_session)) -> dict:
    return await svc.telegram_view(session)


@router.put("")
async def update_telegram(
    request: Request,
    patch: dict[str, Any] = Body(...),
    auth: str = Depends(require_ops_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    source = f"{client_desc(request)} auth={auth}"
    try:
        changed = await svc.update_telegram(session, patch, source=source)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # 只記欄位名，不記值（bot_token 為機密）
    audit.info("ops 稽核 允許 Telegram 設定 %s %s", ",".join(changed) or "-", source)
    return await svc.telegram_view(session)


@router.post("/test")
async def send_test(
    request: Request,
    auth: str = Depends(require_ops_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    config = await svc.resolve_telegram(session)
    try:
        result = await svc.send_test_message(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TelegramError as e:
        raise HTTPException(status_code=502, detail=f"Telegram 回應錯誤：{e}")
    audit.info("ops 稽核 允許 Telegram 測試訊息 %s auth=%s", client_desc(request), auth)
    return result


@router.post("/detect-chats")
async def detect_chats(
    body: DetectRequest,
    auth: str = Depends(require_ops_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    config = await svc.resolve_telegram(session)
    try:
        chats = await svc.detect_chats(config, body.bot_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TelegramError as e:
        raise HTTPException(status_code=502, detail=f"Telegram 回應錯誤：{e}")
    return {"chats": chats}
