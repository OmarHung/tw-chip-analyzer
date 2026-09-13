"""updated_at on signal_snapshot / daily_price / corporate_action (docs/12 Phase 5)

Revision ID: a7d0e4f2b6c8
Revises: f6c9d3e1a5b7
Create Date: 2026-09-13 21:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7d0e4f2b6c8'
down_revision: Union[str, Sequence[str], None] = 'f6c9d3e1a5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("signal_snapshot", "daily_price", "corporate_action")


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(t, sa.Column('updated_at', sa.DateTime(timezone=True),
                                   server_default=sa.text('now()'), nullable=True))
        op.create_index(f'ix_{t}_updated_at', t, ['updated_at'])


def downgrade() -> None:
    for t in _TABLES:
        op.drop_index(f'ix_{t}_updated_at', table_name=t)
        op.drop_column(t, 'updated_at')
