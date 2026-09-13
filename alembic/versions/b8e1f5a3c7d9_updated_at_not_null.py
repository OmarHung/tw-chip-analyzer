"""updated_at NOT NULL on signal_snapshot / daily_price / corporate_action (docs/13 Phase 2)

a7d0e4f2b6c8 已推到共享 main，建欄時為 nullable=True，與 ORM（UpdatedAtMixin 非 Optional）
不一致；NULL 也會讓 max(updated_at) 快取失效偵測漏掉該筆。以 corrective migration 修正，
不重寫舊 revision。

Revision ID: b8e1f5a3c7d9
Revises: a7d0e4f2b6c8
Create Date: 2026-09-13 23:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8e1f5a3c7d9'
down_revision: Union[str, Sequence[str], None] = 'a7d0e4f2b6c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("signal_snapshot", "daily_price", "corporate_action")


def upgrade() -> None:
    for t in _TABLES:
        op.execute(sa.text(f"UPDATE {t} SET updated_at = now() WHERE updated_at IS NULL"))
        op.alter_column(t, 'updated_at', existing_type=sa.DateTime(timezone=True),
                        existing_server_default=sa.text('now()'), nullable=False)


def downgrade() -> None:
    # 只放寬約束；已補的值不改回 NULL
    for t in _TABLES:
        op.alter_column(t, 'updated_at', existing_type=sa.DateTime(timezone=True),
                        existing_server_default=sa.text('now()'), nullable=True)
