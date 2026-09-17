"""app_user / user_session：全站登入認證與角色權限（docs/17）

Revision ID: a1b2c3d4e5f6
Revises: c3a9e7d15f42
Create Date: 2026-09-17 10:00:00.000000

升級後行為不變：表是空的 → 認證處於相容模式（讀取開放、寫入沿用 OPS_API_KEY /
本機直連）。建立第一個帳號（scripts/create_user.py 或 /api/auth/users）後全站要求登入。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'c3a9e7d15f42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'app_user',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.String(length=64), nullable=True),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('must_change_password', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_app_user_username'), 'app_user', ['username'], unique=True)

    op.create_table(
        'user_session',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('client', sa.String(length=256), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_user_session_token_hash'), 'user_session', ['token_hash'], unique=True)
    op.create_index(op.f('ix_user_session_user_id'), 'user_session', ['user_id'])
    op.create_index(op.f('ix_user_session_expires_at'), 'user_session', ['expires_at'])


def downgrade() -> None:
    op.drop_index(op.f('ix_user_session_expires_at'), table_name='user_session')
    op.drop_index(op.f('ix_user_session_user_id'), table_name='user_session')
    op.drop_index(op.f('ix_user_session_token_hash'), table_name='user_session')
    op.drop_table('user_session')
    op.drop_index(op.f('ix_app_user_username'), table_name='app_user')
    op.drop_table('app_user')
