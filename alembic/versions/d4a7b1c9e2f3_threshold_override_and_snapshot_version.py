"""threshold_override / threshold_change / signal_snapshot.config_version

Revision ID: d4a7b1c9e2f3
Revises: c3f1a9d2e4b7
Create Date: 2026-09-13 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4a7b1c9e2f3'
down_revision: Union[str, Sequence[str], None] = 'c3f1a9d2e4b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'threshold_override',
        sa.Column('key', sa.String(length=128), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_table(
        'threshold_change',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('key', sa.String(length=128), nullable=False),
        sa.Column('old_value', sa.JSON(), nullable=True),
        sa.Column('new_value', sa.JSON(), nullable=True),
        sa.Column('source', sa.String(length=256), nullable=False),
        sa.Column('data_version', sa.String(length=32), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_threshold_change_key', 'threshold_change', ['key'])
    op.add_column('signal_snapshot', sa.Column('config_version', sa.String(length=32), nullable=True))
    op.create_index('ix_signal_snapshot_config_version', 'signal_snapshot', ['config_version'])


def downgrade() -> None:
    op.drop_index('ix_signal_snapshot_config_version', table_name='signal_snapshot')
    op.drop_column('signal_snapshot', 'config_version')
    op.drop_index('ix_threshold_change_key', table_name='threshold_change')
    op.drop_table('threshold_change')
    op.drop_table('threshold_override')
