"""門檻設定頁的服務層：列出、覆寫、重設、重建提示。

YAML 預設 + DB 覆寫的合併規則在 app.core.threshold_registry；此處負責 DB 讀寫、
修改歷史與讓本 process 的設定快取失效（app.core.config.reload_thresholds）。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import threshold_registry as reg
from app.core.config import get_thresholds, load_yaml_thresholds, reload_thresholds
from app.db.models.features import SignalSnapshot
from app.db.models.settings import ThresholdChange, ThresholdOverride
from app.repositories.upsert import upsert_many


async def load_overrides(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(select(ThresholdOverride.key, ThresholdOverride.value))).all()
    return {k: v for k, v in rows}


def _effective(yaml_data: dict, overrides: dict[str, Any]) -> dict:
    return reg.apply_overrides(yaml_data, overrides)


async def _record(
    session: AsyncSession, key: str, old: Any, new: Any, source: str, overrides: dict[str, Any]
) -> None:
    version = reg.data_version(_effective(load_yaml_thresholds(), overrides))
    session.add(ThresholdChange(
        key=key, old_value=old, new_value=new, source=source, data_version=version,
    ))


async def set_setting(
    session: AsyncSession, key: str, value: Any, *, source: str, unlock: bool = False
) -> Any:
    """驗證後寫入覆寫值並記錄歷史。錯誤以 KeyError / PermissionError / ValueError 拋出。"""
    clean = reg.validate(key, value, unlock=unlock)
    overrides = await load_overrides(session)
    old = overrides.get(key)
    await upsert_many(session, ThresholdOverride, [{"key": key, "value": clean}], ["key"])
    await _record(session, key, old, clean, source, {**overrides, key: clean})
    await session.commit()
    reload_thresholds()
    return clean


async def reset_setting(session: AsyncSession, key: str, *, source: str) -> None:
    """刪除覆寫、回到 YAML 預設（殘留的未知鍵也可重設清除）。"""
    overrides = await load_overrides(session)
    if key not in overrides:
        return
    await session.execute(delete(ThresholdOverride).where(ThresholdOverride.key == key))
    rest = {k: v for k, v in overrides.items() if k != key}
    await _record(session, key, overrides[key], None, source, rest)
    await session.commit()
    reload_thresholds()


async def pending_rebuild(session: AsyncSession, version: str) -> bool:
    """signal_snapshot 是否有任何列不是用目前資料版本產生（含重建前的 NULL）。"""
    stmt = (
        select(SignalSnapshot.id)
        .where(SignalSnapshot.config_version.is_distinct_from(version))
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def list_settings(session: AsyncSession) -> dict:
    yaml_data = load_yaml_thresholds()
    overrides = await load_overrides(session)
    effective = _effective(yaml_data, overrides)
    items = [
        {
            "key": s.key, "label": s.label, "group": s.group, "effect": s.effect,
            "kind": s.kind, "min": s.min, "max": s.max, "choices": list(s.choices),
            "locked": s.locked, "help": s.help,
            "default": reg.get_path(yaml_data, s.key),
            "override": overrides.get(s.key),
            "effective": reg.get_path(effective, s.key),
        }
        for s in reg.REGISTRY.values()
    ]
    version = reg.data_version(effective)
    history = (await session.execute(
        select(ThresholdChange).order_by(ThresholdChange.id.desc()).limit(50)
    )).scalars().all()
    return {
        "items": items,
        # DB 有覆寫但登錄表/YAML 已無此鍵（例如設定改名）：不生效，需人工確認後重設清除
        "orphaned": sorted(k for k in overrides if k not in reg.REGISTRY
                           or reg.get_path(yaml_data, k) is None),
        "data_version": version,
        "pending_rebuild": await pending_rebuild(session, version),
        "history": [
            {"key": h.key, "old": h.old_value, "new": h.new_value, "source": h.source,
             "data_version": h.data_version,
             "changed_at": h.changed_at.isoformat() if h.changed_at else None}
            for h in history
        ],
        # 本 process 目前實際生效的版本（應等於 data_version；不同代表快取未刷新）
        "running_version": reg.data_version(get_thresholds().raw),
    }
