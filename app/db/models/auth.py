"""使用者帳號與登入 session（全站認證，見 docs/17）。

設計取捨：
- 表名 `app_user`：`user` 是 PostgreSQL 保留字，加前綴省掉每處引號。
- session 用 opaque token + DB 紀錄（非 JWT）：要能「立刻」撤銷——停用帳號或改密碼
  後舊 cookie 必須當場失效，JWT 得等過期或另建黑名單，對單機單人系統不划算。
- DB 只存 token 的 SHA-256 指紋（app/core/security.token_fingerprint）。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

ROLE_ADMIN = "admin"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_ADMIN, ROLE_VIEWER)


class User(Base):
    """帳號。role：admin 可寫（設定/工作/回補/帳號管理）、viewer 只讀。"""

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_VIEWER)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # admin 代設密碼後為 True：使用者下次登入須自行改密碼（避免代設密碼長期沿用）
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class UserSession(Base):
    """登入 session。過期由 expires_at 判定，續期見 app/services/auth.resolve_session。"""

    __tablename__ = "user_session"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 建立當下的來源描述（IP / 轉發鏈 / UA 摘要），供稽核對照，不參與驗證
    client: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
