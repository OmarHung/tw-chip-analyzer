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


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_thresholds() -> Thresholds:
    path = get_settings().thresholds_path
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Thresholds(data or {})
