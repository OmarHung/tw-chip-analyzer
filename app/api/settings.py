"""門檻設定 API：讀取免金鑰；修改/重設需 ops 管理者認證並寫稽核 log。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ops_auth import client_desc, require_ops_auth
from app.core.logging import get_logger
from app.db.session import get_session
from app.services import threshold_settings as svc

router = APIRouter(prefix="/api/ops/settings", tags=["ops"])
audit = get_logger("api.ops.audit")


class SettingUpdate(BaseModel):
    value: Any
    unlock: bool = False  # 鎖定權重（未經 OOS 驗證）需明確解鎖


@router.get("")
async def get_settings_view(session: AsyncSession = Depends(get_session)) -> dict:
    return await svc.list_settings(session)


@router.put("/{key}")
async def update_setting(
    key: str,
    body: SettingUpdate,
    request: Request,
    auth: str = Depends(require_ops_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    source = f"{client_desc(request)} auth={auth}"
    try:
        value = await svc.set_setting(session, key, body.value, source=source, unlock=body.unlock)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"不可調整的設定：{key}")
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.info("ops 稽核 允許 設定 %s=%r unlock=%s %s", key, value, body.unlock, source)
    return await svc.list_settings(session)


@router.delete("/{key}")
async def reset_setting(
    key: str,
    request: Request,
    auth: str = Depends(require_ops_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    source = f"{client_desc(request)} auth={auth}"
    await svc.reset_setting(session, key, source=source)
    audit.info("ops 稽核 允許 重設設定 %s %s", key, source)
    return await svc.list_settings(session)
