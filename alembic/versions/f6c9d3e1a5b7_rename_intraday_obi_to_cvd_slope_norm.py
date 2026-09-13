"""rename feature_daily.intraday_obi -> cvd_slope_norm (docs/12 Phase 3B)

保留既有歷史值（純欄位改名）；同時把 UI 覆寫中的 weights.intraday.obi 鍵改名，
避免改名後覆寫靜默失效。

Revision ID: f6c9d3e1a5b7
Revises: e5b8c2d0f4a6
Create Date: 2026-09-13 20:00:00

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'f6c9d3e1a5b7'
down_revision: Union[str, Sequence[str], None] = 'e5b8c2d0f4a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('feature_daily', 'intraday_obi', new_column_name='cvd_slope_norm')
    op.execute(
        "UPDATE threshold_override SET key='weights.intraday.cvd_slope' "
        "WHERE key='weights.intraday.obi'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE threshold_override SET key='weights.intraday.obi' "
        "WHERE key='weights.intraday.cvd_slope'"
    )
    op.alter_column('feature_daily', 'cvd_slope_norm', new_column_name='intraday_obi')
