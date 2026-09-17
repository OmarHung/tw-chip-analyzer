"""密碼雜湊與 session token（標準庫 scrypt，不引入新相依）。

選 scrypt 而非 bcrypt/argon2 的理由：部署路徑（systemd / Docker / macOS 開發機）
都只跑 `pip install -r requirements.txt`，多一個需編譯的 C 套件就多一種裝不起來的
方式；`hashlib.scrypt` 由 OpenSSL 提供，記憶體硬（memory-hard），參數符合 OWASP
建議（N=2^14, r=8, p=1）。雜湊字串自帶參數，日後調參不會讓舊密碼失效。

session token 只在 DB 存 SHA-256 指紋：DB 外洩時無法還原 cookie 值。token 本身有
256 bit 熵（secrets），不需要再對指紋加鹽（無字典攻擊空間）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_SCHEME = "scrypt"
_N = 2**14  # CPU/記憶體成本（16 MiB，低於 hashlib 預設 maxmem 32 MiB）
_R = 8
_P = 1
_SALT_BYTES = 16
_KEY_LEN = 32
_TOKEN_BYTES = 32


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    """回傳 `scrypt$N$r$p$salt$key`（參數內嵌，驗證時照著算）。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_LEN)
    return f"{_SCHEME}${_N}${_R}${_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, encoded: str) -> bool:
    """常數時間比對；格式不符或參數異常一律回 False（不拋例外，避免洩漏細節）。"""
    try:
        scheme, n, r, p, salt, key = encoded.split("$")
        if scheme != _SCHEME:
            return False
        got = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(_unb64(key))
        )
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(got, _unb64(key))


def new_session_token() -> str:
    """cookie 內的 session token（只在發出時存在明文）。"""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def token_fingerprint(token: str) -> str:
    """DB 儲存用的 token 指紋（SHA-256 hex，64 字元）。"""
    return hashlib.sha256(token.encode()).hexdigest()
