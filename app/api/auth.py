"""認證與帳號管理 API（`/api/auth/*`）。

本路由**不掛全站認證依賴**：登入端點必須在未登入時可達。個別端點自行要求權限
（改密碼需本人 session、帳號管理需 admin）。

cookie 規則：httpOnly（JS 讀不到）、SameSite=Lax、https 時自動加 Secure。token 是
不透明亂數，DB 只存 SHA-256 指紋，登出即刪除，改密碼/停用帳號會撤銷所有既有 session。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_deps import (
    client_desc,
    client_ip,
    cookie_name,
    current_principal,
    require_admin,
    require_user,
)
from app.core.logging import get_logger
from app.db.models.auth import ROLES, User
from app.db.session import get_session
from app.services import auth as svc
from app.services.auth import AuthError, KIND_SESSION, LockedOut, Principal

router = APIRouter(prefix="/api/auth", tags=["auth"])
audit = get_logger("api.ops.audit")


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = Field(default="viewer")
    display_name: str | None = None


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    display_name: str | None = None


class PasswordReset(BaseModel):
    new_password: str


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "role": u.role,
        "is_active": u.is_active,
        "must_change_password": u.must_change_password,
        "last_login_at": _iso(u.last_login_at),
        "created_at": _iso(u.created_at),
    }


def _me(principal: Principal, enabled: bool) -> dict:
    return {
        "authenticated": principal.kind == KIND_SESSION,
        "auth_enabled": enabled,
        "username": principal.name,
        "role": principal.role,
        "kind": principal.kind,
        "must_change_password": principal.must_change_password,
    }


def _cookie_secure(request: Request) -> bool:
    mode = str(svc.conf("cookie_secure", "auto")).lower()
    if mode in ("always", "true", "1"):
        return True
    if mode in ("never", "false", "0"):
        return False
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return (proto or request.url.scheme) == "https"


def _set_cookie(response: Response, request: Request, token: str, expires_at: dt.datetime) -> None:
    max_age = max(int((expires_at - dt.datetime.now(dt.timezone.utc)).total_seconds()), 0)
    response.set_cookie(
        key=cookie_name(),
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
    )


@router.get("/state")
async def auth_state(session: AsyncSession = Depends(get_session)) -> dict:
    """登入頁用：是否已建立帳號（未建立時全站處於相容模式，需先用 CLI 建第一個帳號）。"""
    enabled = await svc.auth_enabled(session)
    return {"auth_enabled": enabled, "has_users": enabled}


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    who = client_desc(request)
    try:
        user = await svc.authenticate(session, body.username, body.password, client_ip(request))
    except LockedOut as e:
        audit.warning("ops 稽核 拒絕 登入 %s %s：鎖定中", body.username, who)
        raise HTTPException(status_code=429, detail=str(e))
    except AuthError as e:
        audit.warning("ops 稽核 拒絕 登入 %s %s", body.username, who)
        raise HTTPException(status_code=401, detail=str(e))

    ua = request.headers.get("user-agent", "")[:120]
    token, expires_at = await svc.start_session(session, user, f"{who} ua={ua}")
    _set_cookie(response, request, token, expires_at)
    audit.info("ops 稽核 允許 登入 %s(%s) %s", user.username, user.role, who)
    return _me(
        Principal(
            kind=KIND_SESSION, name=user.username, role=user.role, user_id=user.id,
            must_change_password=user.must_change_password,
        ),
        enabled=True,
    )


@router.post("/logout")
async def logout(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> dict:
    token = request.cookies.get(cookie_name())
    if token:
        await svc.revoke_session(session, token)
    response.delete_cookie(key=cookie_name(), path="/")
    audit.info("ops 稽核 允許 登出 %s", client_desc(request))
    return {"ok": True}


@router.get("/me")
async def me(
    principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return _me(principal, enabled=await svc.auth_enabled(session))


@router.post("/password")
async def change_password(
    body: PasswordChange,
    request: Request,
    principal: Principal = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """本人改密碼。舊密碼必須正確；其他裝置的 session 一併登出，目前這台保留。"""
    if principal.kind != KIND_SESSION or principal.user_id is None:
        raise HTTPException(status_code=403, detail="請以帳號登入後再改密碼")
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="帳號不存在")
    try:
        await svc.authenticate(session, user.username, body.current_password, client_ip(request))
        await svc.set_password(
            session, user, body.new_password,
            keep_sessions_for=request.cookies.get(cookie_name()),
        )
    except LockedOut as e:
        raise HTTPException(status_code=429, detail=str(e))
    except AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit.info("ops 稽核 允許 改密碼 %s %s", user.username, client_desc(request))
    return {"ok": True}


# --- 帳號管理（admin） ---


@router.get("/users")
async def list_users(
    _: Principal = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict:
    users = await svc.list_users(session)
    return {"users": [_user_dict(u) for u in users], "roles": list(ROLES)}


@router.post("/users")
async def create_user(
    body: UserCreate,
    request: Request,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        user = await svc.create_user(
            session, body.username, body.password, body.role, body.display_name
        )
    except AuthError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.info(
        "ops 稽核 允許 建立帳號 %s(%s) by=%s %s",
        user.username, user.role, principal.label, client_desc(request),
    )
    return _user_dict(user)


async def _get_target(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="帳號不存在")
    return user


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: int,
    body: UserPatch,
    request: Request,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await _get_target(session, user_id)
    # 護欄：最後一個啟用中的 admin 不可被停用或降級，否則沒人能再管理系統
    losing_admin = (body.is_active is False and user.is_admin) or (
        body.role is not None and body.role != "admin" and user.is_admin
    )
    if losing_admin and await svc.count_active_admins(session, exclude_id=user.id) == 0:
        raise HTTPException(status_code=409, detail="至少需保留一個啟用中的管理者帳號")
    try:
        await svc.update_user(
            session, user, role=body.role, is_active=body.is_active,
            display_name=body.display_name,
        )
    except AuthError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.info(
        "ops 稽核 允許 修改帳號 %s role=%s active=%s by=%s %s",
        user.username, user.role, user.is_active, principal.label, client_desc(request),
    )
    return _user_dict(user)


@router.post("/users/{user_id}/password")
async def reset_password(
    user_id: int,
    body: PasswordReset,
    request: Request,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """管理者代設密碼：對方所有 session 立即失效，下次登入須自行改密碼。"""
    user = await _get_target(session, user_id)
    try:
        await svc.set_password(session, user, body.new_password, must_change=True)
    except AuthError as e:
        raise HTTPException(status_code=422, detail=str(e))
    audit.info(
        "ops 稽核 允許 重設密碼 %s by=%s %s",
        user.username, principal.label, client_desc(request),
    )
    return _user_dict(user)


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await _get_target(session, user_id)
    if principal.user_id == user.id:
        raise HTTPException(status_code=409, detail="不可刪除自己的帳號")
    if user.is_admin and await svc.count_active_admins(session, exclude_id=user.id) == 0:
        raise HTTPException(status_code=409, detail="至少需保留一個啟用中的管理者帳號")
    await svc.delete_user(session, user)
    audit.info(
        "ops 稽核 允許 刪除帳號 %s by=%s %s",
        user.username, principal.label, client_desc(request),
    )
    return {"ok": True}
