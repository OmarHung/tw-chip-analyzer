"""notify_setting: UI 可設定的推播通道（Telegram）

Revision ID: c3a9e7d15f42
Revises: b8e3f5a91c27
Create Date: 2026-09-15 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3a9e7d15f42'
down_revision: Union[str, Sequence[str], None] = 'b8e3f5a91c27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notify_setting',
        sa.Column('channel', sa.String(length=32), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=True),
        sa.Column('bot_token', sa.Text(), nullable=True),
        sa.Column('chat_id', sa.String(length=64), nullable=True),
        sa.Column('actions', sa.JSON(), nullable=True),
        sa.Column('max_items', sa.Integer(), nullable=True),
        sa.Column('max_reasons', sa.Integer(), nullable=True),
        sa.Column('min_turnover', sa.Float(), nullable=True),
        sa.Column('updated_by', sa.String(length=256), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.PrimaryKeyConstraint('channel'),
    )


def downgrade() -> None:
    op.drop_table('notify_setting')
