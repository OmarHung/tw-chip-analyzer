"""feature_daily resistance_high / is_limit_locked (docs/09 BUG-01/03)

Revision ID: c3f1a9d2e4b7
Revises: 1792070d957a
Create Date: 2026-09-13 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3f1a9d2e4b7'
down_revision: Union[str, Sequence[str], None] = '1792070d957a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('feature_daily', sa.Column('resistance_high', sa.Numeric(14, 4), nullable=True))
    op.add_column('feature_daily', sa.Column('is_limit_locked', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('feature_daily', 'is_limit_locked')
    op.drop_column('feature_daily', 'resistance_high')
