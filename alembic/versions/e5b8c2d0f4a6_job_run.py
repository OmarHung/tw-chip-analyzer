"""job_run (UI 觸發腳本執行紀錄)

Revision ID: e5b8c2d0f4a6
Revises: d4a7b1c9e2f3
Create Date: 2026-09-13 19:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5b8c2d0f4a6'
down_revision: Union[str, Sequence[str], None] = 'd4a7b1c9e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'job_run',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.String(length=64), nullable=False),
        sa.Column('params', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('exit_code', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=256), nullable=False),
        sa.Column('data_version', sa.String(length=32), nullable=True),
        sa.Column('output', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_job_run_task_id', 'job_run', ['task_id'])


def downgrade() -> None:
    op.drop_index('ix_job_run_task_id', table_name='job_run')
    op.drop_table('job_run')
