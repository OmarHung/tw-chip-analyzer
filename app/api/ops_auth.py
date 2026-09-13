"""ops 寫入型端點的管理者認證（docs/09 RISK-01）。

CORS 只限制瀏覽器跨來源，擋不住直接 HTTP 呼叫；回補會跑數分鐘並消耗 Shioaji 配額，
經 nginx / tailscale 對外開放時任何人都能觸發。規則：

- 設了 `OPS_API_KEY`：必須帶正確 `X-Ops-Key`（hmac.compare_digest 常數時間比對）。
- 沒設：只接受本機直連——client 為 loopback **且**無轉發標頭。反向代理轉發時
  client 同樣是 127.0.0.1，只看來源 IP 會被整個 tailnet 繞過。

每次允許/拒絕都寫「ops 稽核」log（來源、轉發鏈、認證方式），金鑰本身不入 log。
"""
from __future__ import annotations

import hmac
import ipaddress

from fastapi import Header, HTTPException, Request

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("api.ops.audit")

_FORWARD_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")


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


async def require_ops_auth(
    request: Request, x_ops_key: str | None = Header(default=None)
) -> str:
    """回傳認證方式（"key" / "local"）；未通過拋 401/403 並寫稽核 log。"""
    expected = get_settings().ops_api_key
    who = client_desc(request)
    path = request.url.path
    if expected:
        if x_ops_key and hmac.compare_digest(x_ops_key.encode(), expected.encode()):
            return "key"
        logger.warning("ops 稽核 拒絕 %s %s：金鑰缺少或錯誤", path, who)
        raise HTTPException(status_code=401, detail="需要有效的 X-Ops-Key")

    proxied = any(h in request.headers for h in _FORWARD_HEADERS)
    host = request.client.host if request.client else None
    if _is_loopback(host) and not proxied:
        return "local"
    logger.warning("ops 稽核 拒絕 %s %s：未設定 OPS_API_KEY，僅限本機直連", path, who)
    raise HTTPException(
        status_code=403,
        detail="未設定 OPS_API_KEY，回補僅允許本機直連；對外開放請設定金鑰",
    )
