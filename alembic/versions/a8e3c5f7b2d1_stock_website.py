"""stock.website：公司網址（MOPS 基本資料），熱力圖商標用

既有列為 NULL，下次 EOD 公司基本資料刷新時補上。

Revision ID: a8e3c5f7b2d1
Revises: d4f7a2c9e1b3
Create Date: 2026-09-15 10:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a8e3c5f7b2d1'
down_revision: Union[str, Sequence[str], None] = 'd4f7a2c9e1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('stock', sa.Column('website', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('stock', 'website')
