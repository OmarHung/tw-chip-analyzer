"""ops 寫入型端點的管理者認證（docs/09 RISK-01；全站認證見 app/api/auth_deps）。

保留此模組是為了既有 router 的 import 路徑與稽核字串格式不變：
`require_ops_auth` 回傳身分標記（"session:<帳號>" / "key" / "local"），寫進稽核 log。
"""
from __future__ import annotations

from fastapi import Depends

from app.api.auth_deps import client_desc, require_admin
from app.services.auth import Principal

__all__ = ["client_desc", "require_ops_auth"]


async def require_ops_auth(principal: Principal = Depends(require_admin)) -> str:
    """通過管理者認證並回傳身分標記；未通過由 require_admin 拋 401/403。"""
    return principal.label
