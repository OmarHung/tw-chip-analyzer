"""請求身分解析與權限依賴（session cookie / X-Ops-Key / 相容模式）。

三種通過方式，依序嘗試：

1. **session cookie**：瀏覽器登入後取得，帶使用者與角色（admin / viewer）。
2. **X-Ops-Key**：給排程腳本、curl 等非瀏覽器用戶端，等同 admin。
3. **相容模式**：系統尚未建立任何啟用帳號時，讀取端點放行、寫入端點沿用舊規則
   （設了 OPS_API_KEY 就必須帶金鑰；沒設則只接受本機直連）。建立第一個帳號後
   此模式自動關閉——見 app/services/auth.auth_enabled。

CORS 只限制瀏覽器跨來源，擋不住直接 HTTP 呼叫，所以權限一律在後端判定。
每次拒絕都寫「ops 稽核」log（來源、轉發鏈、原因），金鑰與 token 本身不入 log。
"""
from __future__ import annotations

import hmac
import ipaddress

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_session
from app.services import auth as svc
from app.services.auth import (
    KIND_LOCAL,
    KIND_OPEN,
    KIND_OPS_KEY,
    KIND_SESSION,
    Principal,
)

logger = get_logger("api.ops.audit")

_FORWARD_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")


def cookie_name() -> str:
    return str(svc.conf("cookie_name", "twchip_session"))


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def client_desc(request: Request) -> str:
    host = request.client.host if request.client else "?"
    fwd = request.headers.get("x-forwarded-for")
    return f"client={host}" + (f" forwarded_for={fwd}" if fwd else "")


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def is_proxied(request: Request) -> bool:
    return any(h in request.headers for h in _FORWARD_HEADERS)


async def _resolve_user(session: AsyncSession, token: str | None):
    """解析 session cookie；DB 尚未 migrate 等狀況降級為「未登入」而非 500。"""
    if not token:
        return None
    try:
        return await svc.resolve_session(session, token)
    except Exception as e:  # noqa: BLE001 — 認證表不存在時不該讓整站 500
        await session.rollback()
        logger.warning("ops 稽核 session 解析失敗（降級為未登入）：%s", e)
        return None


async def _auth_enabled(session: AsyncSession) -> bool:
    try:
        return await svc.auth_enabled(session)
    except Exception as e:  # noqa: BLE001 — 同上：未 migrate 時維持相容模式
        await session.rollback()
        logger.warning("ops 稽核 帳號表不可用（維持相容模式）：%s", e)
        return False


async def current_principal(
    request: Request,
    x_ops_key: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """解析身分；未通過拋 401。實際權限由 require_user / require_admin 判定。"""
    token = request.cookies.get(cookie_name())
    user = await _resolve_user(session, token)
    if user is not None:
        return Principal(
            kind=KIND_SESSION,
            name=user.username,
            role=user.role,
            user_id=user.id,
            must_change_password=user.must_change_password,
        )

    expected = get_settings().ops_api_key
    if x_ops_key is not None and expected:
        if hmac.compare_digest(x_ops_key.encode(), expected.encode()):
            return Principal(kind=KIND_OPS_KEY, name="ops-key", role="admin")
        logger.warning("ops 稽核 拒絕 %s %s：金鑰錯誤", request.url.path, client_desc(request))
        raise HTTPException(status_code=401, detail="X-Ops-Key 無效")

    if await _auth_enabled(session):
        logger.warning("ops 稽核 拒絕 %s %s：未登入", request.url.path, client_desc(request))
        raise HTTPException(status_code=401, detail="需要登入")

    # 相容模式：尚未建立帳號。讀取放行，寫入交由 require_admin 依舊規則判定。
    return Principal(kind=KIND_OPEN, name="anonymous", role="viewer")


async def require_user(principal: Principal = Depends(current_principal)) -> Principal:
    """任何已認證身分（含相容模式）皆可讀取。"""
    return principal


async def require_admin(
    request: Request,
    principal: Principal = Depends(current_principal),
) -> Principal:
    """寫入型端點：需 admin 角色；相容模式沿用 OPS_API_KEY / 本機直連舊規則。"""
    if principal.is_admin:
        return principal

    path = request.url.path
    who = client_desc(request)
    if principal.kind == KIND_OPEN:
        if get_settings().ops_api_key:
            logger.warning("ops 稽核 拒絕 %s %s：金鑰缺少或錯誤", path, who)
            raise HTTPException(status_code=401, detail="需要有效的 X-Ops-Key")
        if _is_loopback(client_ip(request)) and not is_proxied(request):
            return Principal(kind=KIND_LOCAL, name="local", role="admin")
        logger.warning("ops 稽核 拒絕 %s %s：未設定 OPS_API_KEY，僅限本機直連", path, who)
        raise HTTPException(
            status_code=403,
            detail="未設定 OPS_API_KEY，寫入操作僅允許本機直連；對外開放請建立帳號或設定金鑰",
        )

    logger.warning("ops 稽核 拒絕 %s %s：%s 非管理者", path, who, principal.name)
    raise HTTPException(status_code=403, detail="需要管理者（admin）權限")
