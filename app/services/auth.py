"""帳號 / 登入 session 的商業邏輯（API 層只負責 HTTP 與 cookie）。

重要行為（docs/17）：
- **未建立任何啟用帳號時，認證不啟用**：全站行為與加認證前完全相同（讀取開放、
  寫入沿用 OPS_API_KEY / 本機直連規則）。這是為了讓既有部署升級後不會把自己鎖在
  門外，也讓現有測試不必全面改寫；一旦建立第一個帳號，全站立刻要求登入。
- 密碼驗證與雜湊是 CPU-bound（scrypt 約數十毫秒），一律丟到 thread，不卡 event loop。
- 失敗登入以「帳號」與「來源 IP」雙軌計數，任一超限即鎖定一段時間（防暴力與帳號列舉）。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings, get_thresholds
from app.core.logging import get_logger
from app.core.security import (
    hash_password,
    new_session_token,
    token_fingerprint,
    verify_password,
)
from app.db.models.auth import ROLES, User, UserSession

logger = get_logger("api.ops.audit")

KIND_SESSION = "session"
KIND_OPS_KEY = "ops_key"
KIND_LOCAL = "local"
KIND_OPEN = "open"


class AuthError(Exception):
    """登入/改密碼失敗（訊息可直接顯示給使用者，不含機密）。"""


class LockedOut(AuthError):
    """短時間內失敗過多，暫時鎖定。"""


@dataclass(frozen=True)
class Principal:
    """本次請求的身分。kind=open 代表「系統尚未建立帳號」的相容模式。"""

    kind: str
    name: str
    role: str
    user_id: int | None = None
    must_change_password: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def label(self) -> str:
        """稽核 log 用的身分標記（沿用舊格式：金鑰=key、本機直連=local）。"""
        if self.kind == KIND_SESSION:
            return f"session:{self.name}"
        return {KIND_OPS_KEY: "key", KIND_LOCAL: "local"}.get(self.kind, self.kind)


def conf(key: str, default: Any) -> Any:
    return get_thresholds().get("auth", key, default=default)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --- 登入失敗限流（單行程記憶體；重啟即清空，對單機部署足夠） ---

_failures: dict[str, tuple[int, float]] = {}


def _throttle_key(username: str, client_ip: str | None) -> list[str]:
    keys = [f"user:{username.strip().lower()}"]
    if client_ip:
        keys.append(f"ip:{client_ip}")
    return keys


def check_lockout(username: str, client_ip: str | None) -> None:
    """已達失敗上限且仍在鎖定期內 → LockedOut。"""
    limit = int(conf("max_failed_attempts", 5))
    now = time.monotonic()
    for k in _throttle_key(username, client_ip):
        count, until = _failures.get(k, (0, 0.0))
        if count >= limit and until > now:
            raise LockedOut(f"失敗次數過多，請於 {int(until - now) + 1} 秒後再試")


def record_failure(username: str, client_ip: str | None) -> None:
    """累計失敗。視窗內連續累加，超過視窗未再失敗則重新計數。"""
    window = float(conf("lockout_seconds", 300))
    now = time.monotonic()
    for k in _throttle_key(username, client_ip):
        count, until = _failures.get(k, (0, 0.0))
        count = count + 1 if until > now else 1
        _failures[k] = (count, now + window)


def clear_failures(username: str, client_ip: str | None) -> None:
    for k in _throttle_key(username, client_ip):
        _failures.pop(k, None)


def reset_throttle() -> None:
    """測試用：清空限流狀態。"""
    _failures.clear()


# --- 認證是否啟用 ---

_has_users_cache: tuple[float, bool] | None = None
_HAS_USERS_TTL = 5.0


def invalidate_auth_cache() -> None:
    global _has_users_cache
    _has_users_cache = None


async def auth_enabled(session: AsyncSession) -> bool:
    """是否已有啟用中的帳號（= 全站是否強制登入）。"""
    global _has_users_cache
    if not get_settings().is_test and _has_users_cache is not None:
        ts, value = _has_users_cache
        if time.monotonic() - ts < _HAS_USERS_TTL:
            return value
    count = await session.scalar(
        select(func.count()).select_from(User).where(User.is_active.is_(True))
    )
    value = bool(count)
    _has_users_cache = (time.monotonic(), value)
    return value


# --- 帳號管理 ---


def validate_password(password: str) -> None:
    min_len = int(conf("min_password_length", 10))
    if len(password or "") < min_len:
        raise AuthError(f"密碼至少需 {min_len} 個字元")


def normalize_username(username: str) -> str:
    name = (username or "").strip()
    if not (3 <= len(name) <= 64) or not all(c.isalnum() or c in "._-" for c in name):
        raise AuthError("帳號需為 3~64 字元的英數與 . _ - 組合")
    return name


async def get_user(session: AsyncSession, username: str) -> User | None:
    return await session.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))


async def list_users(session: AsyncSession) -> list[User]:
    return list((await session.scalars(select(User).order_by(User.id))).all())


async def create_user(
    session: AsyncSession,
    username: str,
    password: str,
    role: str,
    display_name: str | None = None,
    must_change_password: bool = False,
) -> User:
    name = normalize_username(username)
    if role not in ROLES:
        raise AuthError(f"角色需為 {' / '.join(ROLES)}")
    validate_password(password)
    if await get_user(session, name):
        raise AuthError(f"帳號已存在：{name}")
    user = User(
        username=name,
        display_name=(display_name or None),
        password_hash=await asyncio.to_thread(hash_password, password),
        role=role,
        is_active=True,
        must_change_password=must_change_password,
    )
    session.add(user)
    await session.commit()
    invalidate_auth_cache()
    return user


async def set_password(
    session: AsyncSession, user: User, password: str, must_change: bool = False,
    keep_sessions_for: str | None = None,
) -> None:
    """改密碼並撤銷所有既有 session（keep_sessions_for＝保留的 token，讓本人改密碼後不必重登）。"""
    validate_password(password)
    user.password_hash = await asyncio.to_thread(hash_password, password)
    user.must_change_password = must_change
    await revoke_user_sessions(session, user.id, keep_token=keep_sessions_for)
    await session.commit()


async def update_user(
    session: AsyncSession, user: User, *, role: str | None = None,
    is_active: bool | None = None, display_name: str | None = None,
) -> User:
    if role is not None:
        if role not in ROLES:
            raise AuthError(f"角色需為 {' / '.join(ROLES)}")
        user.role = role
    if display_name is not None:
        user.display_name = display_name or None
    if is_active is not None:
        user.is_active = is_active
        if not is_active:
            await revoke_user_sessions(session, user.id)
    await session.commit()
    invalidate_auth_cache()
    return user


async def count_active_admins(session: AsyncSession, exclude_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(User).where(
        User.is_active.is_(True), User.role == "admin"
    )
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return int(await session.scalar(stmt) or 0)


async def delete_user(session: AsyncSession, user: User) -> None:
    await revoke_user_sessions(session, user.id)
    await session.delete(user)
    await session.commit()
    invalidate_auth_cache()


# --- 登入 / session ---


async def authenticate(
    session: AsyncSession, username: str, password: str, client_ip: str | None
) -> User:
    check_lockout(username, client_ip)
    user = await get_user(session, username)
    # 查無帳號時仍跑一次雜湊：讓「帳號不存在」與「密碼錯誤」的耗時相近，避免帳號列舉
    encoded = user.password_hash if user else hash_password("dummy-timing-guard")
    ok = await asyncio.to_thread(verify_password, password, encoded)
    if not user or not ok or not user.is_active:
        record_failure(username, client_ip)
        raise AuthError("帳號或密碼錯誤")
    clear_failures(username, client_ip)
    return user


async def start_session(
    session: AsyncSession, user: User, client: str | None
) -> tuple[str, dt.datetime]:
    token = new_session_token()
    ttl_hours = float(conf("session_ttl_hours", 336))
    expires_at = _now() + dt.timedelta(hours=ttl_hours)
    session.add(
        UserSession(
            token_hash=token_fingerprint(token),
            user_id=user.id,
            client=(client or None),
            expires_at=expires_at,
        )
    )
    user.last_login_at = _now()
    await session.commit()
    await purge_expired(session)
    return token, expires_at


async def resolve_session(session: AsyncSession, token: str) -> User | None:
    """回傳有效 session 對應的使用者；過期/撤銷/帳號停用一律 None。順便滑動續期。"""
    if not token:
        return None
    row = await session.scalar(
        select(UserSession).where(UserSession.token_hash == token_fingerprint(token))
    )
    if row is None:
        return None
    now = _now()
    if row.expires_at <= now:
        await session.delete(row)
        await session.commit()
        return None
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None

    ttl = dt.timedelta(hours=float(conf("session_ttl_hours", 336)))
    renew_within = dt.timedelta(hours=float(conf("renew_within_hours", 168)))
    row.last_seen_at = now
    if row.expires_at - now < renew_within:  # 快到期才續，避免每個請求都寫 DB
        row.expires_at = now + ttl
    await session.commit()
    return user


async def revoke_session(session: AsyncSession, token: str) -> None:
    await session.execute(
        delete(UserSession).where(UserSession.token_hash == token_fingerprint(token))
    )
    await session.commit()


async def revoke_user_sessions(
    session: AsyncSession, user_id: int, keep_token: str | None = None
) -> None:
    stmt = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_token:
        stmt = stmt.where(UserSession.token_hash != token_fingerprint(keep_token))
    await session.execute(stmt)


async def purge_expired(session: AsyncSession) -> None:
    await session.execute(delete(UserSession).where(UserSession.expires_at <= _now()))
    await session.commit()
