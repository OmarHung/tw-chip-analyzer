"""帳號管理 CLI（建立第一個管理者、改密碼、停用、列出）。

網頁的帳號管理需要先有 admin 登入；**第一個帳號只能從這裡建立**（或在尚未建立任何
帳號的相容模式下，由本機直連 / 帶 OPS_API_KEY 呼叫 POST /api/auth/users）。

用法:
  APP_ENV=prod python -m scripts.manage_users create omar --role admin
  APP_ENV=prod python -m scripts.manage_users create alice --role viewer --password-stdin
  APP_ENV=prod python -m scripts.manage_users passwd omar
  APP_ENV=prod python -m scripts.manage_users role alice admin
  APP_ENV=prod python -m scripts.manage_users disable alice / enable alice
  APP_ENV=prod python -m scripts.manage_users delete alice
  APP_ENV=prod python -m scripts.manage_users list

密碼預設互動輸入（不回顯、需確認兩次），避免留在 shell 歷史；自動化可用
`--password-stdin`（從標準輸入讀一行）。
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.core.config import get_settings
from app.db.models.auth import ROLES
from app.db.session import get_sessionmaker
from app.services import auth as svc


def _read_password(from_stdin: bool, prompt: str = "密碼") -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass(f"{prompt}：")
    if first != getpass.getpass(f"{prompt}（再次輸入）："):
        raise SystemExit("兩次輸入不一致")
    return first


async def _create(args) -> int:
    password = _read_password(args.password_stdin)
    async with get_sessionmaker()() as s:
        user = await svc.create_user(s, args.username, password, args.role, args.display_name)
        print(f"已建立 {user.username}（{user.role}）")
    return 0


async def _passwd(args) -> int:
    password = _read_password(args.password_stdin, "新密碼")
    async with get_sessionmaker()() as s:
        user = await svc.get_user(s, args.username)
        if user is None:
            raise SystemExit(f"查無帳號：{args.username}")
        await svc.set_password(s, user, password)
        print(f"已更新 {user.username} 的密碼（該帳號既有登入 session 全部失效）")
    return 0


async def _role(args) -> int:
    async with get_sessionmaker()() as s:
        user = await svc.get_user(s, args.username)
        if user is None:
            raise SystemExit(f"查無帳號：{args.username}")
        await svc.update_user(s, user, role=args.role)
        print(f"{user.username} → {user.role}")
    return 0


async def _set_active(args, active: bool) -> int:
    async with get_sessionmaker()() as s:
        user = await svc.get_user(s, args.username)
        if user is None:
            raise SystemExit(f"查無帳號：{args.username}")
        if not active and user.is_admin and await svc.count_active_admins(s, exclude_id=user.id) == 0:
            raise SystemExit("至少需保留一個啟用中的管理者帳號")
        await svc.update_user(s, user, is_active=active)
        print(f"{user.username} → {'啟用' if active else '停用'}")
    return 0


async def _delete(args) -> int:
    async with get_sessionmaker()() as s:
        user = await svc.get_user(s, args.username)
        if user is None:
            raise SystemExit(f"查無帳號：{args.username}")
        last_admin = user.is_admin and await svc.count_active_admins(s, exclude_id=user.id) == 0
        if last_admin and not args.force:
            raise SystemExit(
                "這是最後一個管理者帳號；刪除後全站會回到相容模式"
                "（讀取開放、寫入僅限本機/金鑰）。確定請加 --force"
            )
        await svc.delete_user(s, user)
        print(f"已刪除 {args.username}（登入 session 一併移除）")
    return 0


async def _list(_args) -> int:
    async with get_sessionmaker()() as s:
        users = await svc.list_users(s)
    if not users:
        print("（尚無帳號：全站處於相容模式，讀取端點未受保護）")
        return 0
    print(f"{'帳號':<20}{'角色':<8}{'狀態':<6}最後登入")
    for u in users:
        last = u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "-"
        print(f"{u.username:<20}{u.role:<8}{'啟用' if u.is_active else '停用':<6}{last}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="帳號管理（建立/改密碼/角色/停用/列出）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def _with_name(sp):
        sp.add_argument("username")
        return sp

    c = _with_name(sub.add_parser("create", help="建立帳號"))
    c.add_argument("--role", default="admin", choices=list(ROLES))
    c.add_argument("--display-name", default=None)
    c.add_argument("--password-stdin", action="store_true", help="從標準輸入讀密碼")
    c.set_defaults(fn=_create)

    w = _with_name(sub.add_parser("passwd", help="重設密碼（該帳號所有 session 失效）"))
    w.add_argument("--password-stdin", action="store_true")
    w.set_defaults(fn=_passwd)

    r = _with_name(sub.add_parser("role", help="修改角色"))
    r.add_argument("role", choices=list(ROLES))
    r.set_defaults(fn=_role)

    d = _with_name(sub.add_parser("delete", help="刪除帳號"))
    d.add_argument("--force", action="store_true", help="允許刪除最後一個管理者（回到相容模式）")
    d.set_defaults(fn=_delete)
    _with_name(sub.add_parser("disable", help="停用帳號")).set_defaults(
        fn=lambda a: _set_active(a, False)
    )
    _with_name(sub.add_parser("enable", help="啟用帳號")).set_defaults(
        fn=lambda a: _set_active(a, True)
    )
    sub.add_parser("list", help="列出帳號").set_defaults(fn=_list)

    args = p.parse_args()
    print(f"[env={get_settings().app_env}]", file=sys.stderr)
    try:
        return asyncio.run(args.fn(args))
    except svc.AuthError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    raise SystemExit(main())
