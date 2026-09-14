"""raw_tick (data_date, symbol) index for coverage stats

系統頁涵蓋度統計原本對 raw_tick 做全表 count/group by,線上 4,100 萬列、1 vCPU、
PG page cache 冷掉時要跑數十秒到數分鐘,把 API 與 SSR 一起拖慢(2026-09-15 首頁 504
事故)。查詢已改為 loose index scan + 限日期範圍,兩者都需要這條 (data_date, symbol)
索引才會走 index-only scan。

線上注意:raw_tick 很大,這條索引要建數分鐘且會佔約 1~2 GB。用 CONCURRENTLY 以免
鎖住逐筆匯入;CONCURRENTLY 不能在交易內執行,故切 autocommit block。

Revision ID: b8e3f5a91c27
Revises: a8e3c5f7b2d1
Create Date: 2026-09-15 03:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b8e3f5a91c27'
down_revision: Union[str, Sequence[str], None] = 'a8e3c5f7b2d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_raw_tick_date_symbol "
            "ON raw_tick (data_date, symbol)"
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_raw_tick_date_symbol")
