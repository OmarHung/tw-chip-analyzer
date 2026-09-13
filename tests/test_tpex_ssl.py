"""TPEx TLS：部分節點只送網站憑證、漏送 TWCA 中繼憑證（2026-09-14 線上 vultr 實測），
必須以打包的中繼憑證補鏈，而非關閉驗證。全檔不連外網。"""
from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

from app.connectors import tpex

EXPECTED_INTERMEDIATES = {"TWCA SSL Certification Authority", "TWCA CYBER Root CA"}


def _cn(cert: dict) -> str | None:
    """系統信任庫有些根憑證沒有 commonName（只有 OU），回 None。"""
    return next((v for rdn in cert["subject"] for k, v in rdn if k == "commonName"), None)


def test_bundle_contains_twca_intermediates():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cafile=str(tpex.TWCA_INTERMEDIATES))
    assert {_cn(c) for c in ctx.get_ca_certs()} == EXPECTED_INTERMEDIATES


def test_bundle_is_not_gitignored():
    """*.pem 預設被 .gitignore 忽略（Shioaji 憑證）；此檔若被忽略，線上 git pull 缺檔 → API import 失敗。"""
    if shutil.which("git") is None:
        pytest.skip("需要 git")
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["git", "check-ignore", "-q", str(tpex.TWCA_INTERMEDIATES)], cwd=root)
    assert r.returncode == 1, "twca_intermediates.pem 被 .gitignore 忽略"


def test_tpex_context_keeps_verification_and_loads_intermediates():
    ctx = tpex.build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    assert EXPECTED_INTERMEDIATES <= {_cn(c) for c in ctx.get_ca_certs()}


# ---------------------------------------------------------------- 機制：只送網站憑證的伺服器

def _openssl(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def pki(tmp_path: Path) -> Path:
    """測試用 root → intermediate → leaf（CN=localhost）。"""
    if shutil.which("openssl") is None:
        pytest.skip("需要 openssl CLI 產生測試憑證")
    (tmp_path / "ca.ext").write_text(
        "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n"
    )
    (tmp_path / "leaf.ext").write_text(
        "basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost\n"
    )
    for name in ("root", "inter", "leaf"):
        _openssl("req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{name}.key",
                 "-subj", f"/CN={'localhost' if name == 'leaf' else 'test-' + name}",
                 "-out", f"{name}.csr", cwd=tmp_path)
    _openssl("x509", "-req", "-in", "root.csr", "-signkey", "root.key", "-days", "2",
             "-extfile", "ca.ext", "-out", "root.pem", cwd=tmp_path)
    _openssl("x509", "-req", "-in", "inter.csr", "-CA", "root.pem", "-CAkey", "root.key",
             "-CAcreateserial", "-days", "2", "-extfile", "ca.ext", "-out", "inter.pem", cwd=tmp_path)
    _openssl("x509", "-req", "-in", "leaf.csr", "-CA", "inter.pem", "-CAkey", "inter.key",
             "-CAcreateserial", "-days", "2", "-extfile", "leaf.ext", "-out", "leaf.pem", cwd=tmp_path)
    return tmp_path


def _handshake(pki: Path, client_ctx: ssl.SSLContext) -> None:
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(pki / "leaf.pem", pki / "leaf.key")  # 只送網站憑證，不含中繼
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def serve():
        conn, _ = listener.accept()
        try:
            with server_ctx.wrap_socket(conn, server_side=True):
                pass
        except (ssl.SSLError, OSError):
            pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            with client_ctx.wrap_socket(sock, server_hostname="localhost"):
                pass
    finally:
        t.join(timeout=5)
        listener.close()


def test_leaf_only_server_fails_without_intermediate(pki):
    ctx = tpex.build_ssl_context(extra_cafiles=())
    ctx.load_verify_locations(cafile=str(pki / "root.pem"))
    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(pki, ctx)


def test_leaf_only_server_succeeds_with_bundled_intermediate(pki):
    ctx = tpex.build_ssl_context(extra_cafiles=(pki / "inter.pem",))
    ctx.load_verify_locations(cafile=str(pki / "root.pem"))
    _handshake(pki, ctx)
