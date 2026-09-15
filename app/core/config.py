"""應用設定。

- 環境/機密：從 .env 讀取（pydantic-settings）
- 交易門檻：從 config/thresholds.yaml 讀取（型別化 dataclass）

原則（見 docs/07、CLAUDE.md）：所有 threshold 一律 config 化，程式禁止硬編碼。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_THRESHOLDS_PATH = PROJECT_ROOT / "config" / "thresholds.yaml"


class Settings(BaseSettings):
    """環境層設定（機密、連線字串）。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "dev"
    database_url: str = "postgresql+psycopg://omar@localhost:5432/twchip"
    test_database_url: str = "postgresql+psycopg://omar@localhost:5432/twchip_test"

    # Shioaji（Phase 3 才會用到，這裡先預留）
    sj_api_key: str = ""
    sj_sec_key: str = ""
    sj_ca_path: str = ""
    sj_ca_passwd: str = ""

    # ops 寫入型端點（回補等會耗時/耗 Shioaji 配額）的管理金鑰，前端以 X-Ops-Key 帶入。
    # 留空 = 只接受本機直連（無反向代理）；經 nginx / tailscale 對外開放時務必設定。
    ops_api_key: str = ""

    # Telegram 推播（每日 EOD 後推「新進 BUY / AVOID」）。兩者皆設才會送；
    # 開關與格式參數在 config/thresholds.yaml 的 notify.telegram。
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    thresholds_path: Path = Field(default=DEFAULT_THRESHOLDS_PATH)

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    @property
    def active_database_url(self) -> str:
        return self.test_database_url if self.is_test else self.database_url


class Thresholds:
    """交易/籌碼門檻（由 YAML 載入，dict 形式存取）。

    刻意保持 dict-based 以便直接對應 YAML 結構；透過 get() 提供巢狀存取。
    """

    def __init__(self, data: dict[str, Any]):
        self._data = data

    @property
    def raw(self) -> dict[str, Any]:
        """合併覆寫後的完整設定 dict（供 data_version / 設定頁顯示）。"""
        return self._data

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    @property
    def signal(self) -> dict[str, Any]:
        return self._data.get("signal", {})

    @property
    def risk(self) -> dict[str, Any]:
        return self._data.get("risk", {})

    @property
    def entry_filter(self) -> dict[str, Any]:
        return self._data.get("entry_filter", {})

    @property
    def exit_rules(self) -> dict[str, Any]:
        return self._data.get("exit", {})

    @property
    def large_trade(self) -> dict[str, Any]:
        return self._data.get("large_trade", {})

    @property
    def intraday_batch(self) -> dict[str, Any]:
        return self._data.get("intraday_batch", {})

    @property
    def tdcc(self) -> dict[str, Any]:
        return self._data.get("tdcc", {})

    @property
    def divergence(self) -> dict[str, Any]:
        return self._data.get("divergence", {})

    @property
    def cost_basis(self) -> dict[str, Any]:
        return self._data.get("cost_basis", {})

    @property
    def schedule(self) -> dict[str, Any]:
        return self._data.get("schedule", {})

    @property
    def weights(self) -> dict[str, Any]:
        return self._data.get("weights", {})

    @property
    def backtest(self) -> dict[str, Any]:
        return self._data.get("backtest", {})

    @property
    def market_regime(self) -> dict[str, Any]:
        return self._data.get("market_regime", {})

    @property
    def scoring(self) -> dict[str, Any]:
        return self._data.get("scoring", {})


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml_thresholds() -> dict[str, Any]:
    """只讀 YAML（預設值與完整結構），不含 DB 覆寫。"""
    with open(get_settings().thresholds_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _overrides_enabled() -> bool:
    # 測試環境一律只用 YAML：每個 test 重建 schema，且測試不應受 DB 殘留覆寫影響
    return not get_settings().is_test


def _load_overrides_sync() -> dict[str, Any]:
    """同步讀 DB 覆寫值（get_thresholds 是同步 API，腳本/子行程也走這裡）。

    表不存在（尚未 migrate）或 DB 連不上時退回純 YAML 並記 log，不讓設定讀取拖垮啟動。
    """
    from sqlalchemy import create_engine, text

    from app.core.logging import get_logger

    engine = create_engine(get_settings().active_database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("select key, value from threshold_override")).all()
        return {k: v for k, v in rows}
    except Exception as e:  # noqa: BLE001 — 設定讀取失敗必須降級到 YAML
        get_logger("core.config").warning("讀取 DB 門檻覆寫失敗，改用 YAML 預設：%s", e)
        return {}
    finally:
        engine.dispose()


@lru_cache
def get_thresholds() -> Thresholds:
    """YAML 預設 + DB 覆寫（僅登錄表中的鍵）。修改覆寫後呼叫 reload_thresholds()。"""
    from app.core.threshold_registry import apply_overrides

    data = load_yaml_thresholds()
    if _overrides_enabled():
        data = apply_overrides(data, _load_overrides_sync())
    return Thresholds(data)


def reload_thresholds() -> None:
    """清掉本 process 的設定快取；下次 get_thresholds() 重新讀 YAML + DB。

    API 與排程在同一 process（單 worker），UI 改設定後呼叫即全域生效；
    子行程（腳本按鈕）每次啟動都重新讀取，不需通知。
    """
    get_thresholds.cache_clear()
