"""mops review fixes: TPEx canonical market, provenance, holding coverage scope (docs/15)

只動 Phase 2 MOPS 四張表，不碰 Phase 1 表（含 stock）與正式分數資料。

1. mops_fetch_coverage 加 scope_key（轉讓 `*`、持股 = 個股代號），唯一鍵改為
   (dataset, market, data_date, scope_key)，讓持股「查無資料」頁也能記涵蓋。
2. market 'TPEX' → 'TPEx'（同 stock.market）。raw 兩表唯一鍵不含 market，不會衝突；
   coverage 若同鍵已有 'TPEx' 列（兩者都代表成功抓取），刪除 'TPEX' 重複列後再更新。
3. raw / coverage 加首次觀測 provenance：ingestion_mode（既有列 = 'unknown'，無法事後判定是
   forward 或 backfill，故不進 honest OOS）、observed_at（既有列取 created_at / fetched_at）。
4. mops_shadow_feature_daily 加 holding_provenance / transfer_provenance（既有列 = 'unknown'）。

server_default 只用來填既有列，隨即移除，與 ORM（無 server_default、NOT NULL）一致；
新寫入必須明確帶 provenance。

downgrade：刪除持股涵蓋列（scope_key <> '*'，否則舊唯一鍵衝突）與新增欄位。
market 正規化不回復（'TPEX' 是 bug，回復只會重現錯誤）。

Revision ID: d4f7a2c9e1b3
Revises: c9d2e7f1a3b5
Create Date: 2026-09-14 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4f7a2c9e1b3'
down_revision: Union[str, Sequence[str], None] = 'c9d2e7f1a3b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RAW = ("insider_holding_monthly", "insider_transfer_declaration")
_OBSERVED_FROM = {
    "insider_holding_monthly": "created_at",
    "insider_transfer_declaration": "created_at",
    "mops_fetch_coverage": "fetched_at",
}


def _add_filled(table: str, column: sa.Column, fill: str) -> None:
    """NOT NULL 欄：先以 server_default 填既有列，再移除 default（與 ORM 一致）。"""
    column.server_default = sa.DefaultClause(sa.text(fill))
    op.add_column(table, column)
    op.alter_column(table, column.name, server_default=None)


def upgrade() -> None:
    # 1. coverage scope_key + 唯一鍵
    _add_filled("mops_fetch_coverage", sa.Column("scope_key", sa.String(16), nullable=False), "'*'")
    op.drop_constraint("uq_mops_fetch_coverage", "mops_fetch_coverage", type_="unique")
    op.create_unique_constraint(
        "uq_mops_fetch_coverage", "mops_fetch_coverage",
        ["dataset", "market", "data_date", "scope_key"],
    )

    # 2. TPEX → TPEx
    op.execute("""
        DELETE FROM mops_fetch_coverage u
        USING mops_fetch_coverage c
        WHERE u.market = 'TPEX' AND c.market = 'TPEx'
          AND c.dataset = u.dataset AND c.data_date = u.data_date AND c.scope_key = u.scope_key
    """)
    for table in (*_RAW, "mops_fetch_coverage"):
        op.execute(f"UPDATE {table} SET market = 'TPEx' WHERE market = 'TPEX'")

    # 3. provenance（首次觀測）
    for table, src in _OBSERVED_FROM.items():
        _add_filled(table, sa.Column("ingestion_mode", sa.String(16), nullable=False), "'unknown'")
        op.add_column(table, sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True))
        op.execute(f"UPDATE {table} SET observed_at = {src}")
        op.alter_column(table, "observed_at", nullable=False)

    # 4. shadow feature provenance
    for col in ("holding_provenance", "transfer_provenance"):
        _add_filled("mops_shadow_feature_daily", sa.Column(col, sa.String(24), nullable=False),
                    "'unknown'")


def downgrade() -> None:
    for col in ("transfer_provenance", "holding_provenance"):
        op.drop_column("mops_shadow_feature_daily", col)
    for table in _OBSERVED_FROM:
        op.drop_column(table, "observed_at")
        op.drop_column(table, "ingestion_mode")
    op.execute("DELETE FROM mops_fetch_coverage WHERE scope_key <> '*'")
    op.drop_constraint("uq_mops_fetch_coverage", "mops_fetch_coverage", type_="unique")
    op.create_unique_constraint(
        "uq_mops_fetch_coverage", "mops_fetch_coverage", ["dataset", "market", "data_date"],
    )
    op.drop_column("mops_fetch_coverage", "scope_key")
