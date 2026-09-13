"""PostgreSQL 冪等 upsert 工具（ON CONFLICT DO UPDATE）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

# PostgreSQL 單一語句參數上限 65535；依欄位數換算安全批次列數。
_MAX_PARAMS = 60000


def _chunks(rows: list[dict], n_cols: int):
    size = max(1, _MAX_PARAMS // max(1, n_cols))
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


async def upsert_many(
    session: AsyncSession,
    model: type,
    rows: list[dict[str, Any]],
    index_elements: list[str],
    update_columns: list[str] | None = None,
) -> int:
    """對 model 批次 upsert。

    index_elements：唯一鍵欄位（對應 UniqueConstraint 欄位）。
    update_columns：衝突時要更新的欄位；預設更新除唯一鍵外的所有欄位。
    回傳處理筆數。
    """
    if not rows:
        return 0

    n_cols = len(rows[0])
    if update_columns is None:
        update_columns = [c for c in rows[0].keys() if c not in index_elements]
    for chunk in _chunks(rows, n_cols):
        stmt = insert(model).values(chunk)
        set_ = {c: getattr(stmt.excluded, c) for c in update_columns}
        # Core upsert 不保證觸發 ORM onupdate：有 updated_at 的表在衝突更新時明確設 DB now()，
        # 否則同筆覆寫偵測不到（前瞻驗證快取失效依賴它，docs/12 Phase 5）
        if "updated_at" in model.__table__.columns and "updated_at" not in set_:
            set_["updated_at"] = func.now()
        stmt = stmt.on_conflict_do_update(index_elements=index_elements, set_=set_)
        await session.execute(stmt)
    return len(rows)


async def upsert_ignore(
    session: AsyncSession,
    model: type,
    rows: list[dict[str, Any]],
    index_elements: list[str],
) -> int:
    """衝突時不更新（ON CONFLICT DO NOTHING）。用於補 FK 主檔 stub。"""
    if not rows:
        return 0
    n_cols = len(rows[0])
    for chunk in _chunks(rows, n_cols):
        stmt = insert(model).values(chunk).on_conflict_do_nothing(
            index_elements=index_elements
        )
        await session.execute(stmt)
    return len(rows)

