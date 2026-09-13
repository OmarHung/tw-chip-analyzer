"""Phase 2 MOPS 資料表（shadow-only，見 docs/14）。

- insider_holding_monthly：董監/經理人/大股東持股與質押（月），保留原始職稱、姓名與百分比。
- insider_transfer_declaration：內部人持股轉讓事前申報（日），含解析後的修正關係。
- mops_fetch_coverage：抓取涵蓋紀錄（含零筆日/零筆頁），用來分辨「真的沒有」與「沒抓到」。
- mops_shadow_feature_daily：shadow 特徵輸出，刻意與 feature_daily 分表，正式評分完全不讀。

Provenance（docs/14 §7.1）：raw 與涵蓋紀錄都存「首次觀測」的 `ingestion_mode`（forward = 每日 job 向前累積、
backfill = 回補腳本、unknown = 遷移前舊列）與 `observed_at`，重匯/不同來源 upsert 不覆寫。
`observed_at` 是抓取時間，**不是**歷史 `available_at`。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.mixins import AvailabilityMixin, TimestampMixin, UpdatedAtMixin


class ProvenanceMixin:
    """首次觀測模式與時間（只在 insert 時寫入，衝突更新不動）。"""

    ingestion_mode: Mapped[str] = mapped_column(String(16), nullable=False)  # forward/backfill/unknown
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InsiderHoldingMonthly(Base, AvailabilityMixin, TimestampMixin, ProvenanceMixin):
    """一列 = 某公司某月某內部人某職稱的持股（官方同一人多職稱會重複揭露同樣股數）。

    data_date：資料年月的月底。report_date：OpenAPI 的出表日期；MOPS 網頁無此欄，存推定揭露日。
    同月不同 (source, report_date) 視為不同版本，不互相覆寫。股數單位：股。
    row_seq：同 (職稱, 姓名) 在來源中的出現序——法人董事一人多席會各列一次（實測 1101 嘉新兩席），
    同名不同人也可能並存（實測 2412 同名兩列持股不同），故 (職稱, 姓名) 本身不是唯一鍵。
    """

    __tablename__ = "insider_holding_monthly"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "data_date", "source", "report_date", "title", "holder_name", "row_seq",
            name="uq_insider_holding_monthly",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )
    market: Mapped[str] = mapped_column(String(8), nullable=False)  # TWSE / TPEx（同 stock.market）
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # openapi / mops_web
    report_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    holder_name: Mapped[str] = mapped_column(String(256), nullable=False)
    row_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    shares_at_election: Mapped[int | None] = mapped_column(BigInteger)
    current_shares: Mapped[int | None] = mapped_column(BigInteger)
    pledged_shares: Mapped[int | None] = mapped_column(BigInteger)
    pledge_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))  # 官方原值 %
    related_shares: Mapped[int | None] = mapped_column(BigInteger)
    related_pledged_shares: Mapped[int | None] = mapped_column(BigInteger)
    related_pledge_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))


class InsiderTransferDeclaration(Base, AvailabilityMixin, TimestampMixin, UpdatedAtMixin, ProvenanceMixin):
    """一列 = 一筆事前申報（data_date = 申報日期）。股數單位：股。

    row_hash 由不會被事後回寫的核心欄位計算（不含異動情形/未完成轉讓註記），
    OpenAPI 與 MOPS 網頁的同一筆申報會落在同一列。
    superseded_on 是舊申報頁被事後回寫的「本單已於 X 申報變更」——只在 X 揭露後才可使用。
    """

    __tablename__ = "insider_transfer_declaration"
    __table_args__ = (
        UniqueConstraint("symbol", "data_date", "row_hash", name="uq_insider_transfer_declaration"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # 首次寫入來源
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    reporter_role: Mapped[str] = mapped_column(String(64), nullable=False)
    reporter_name: Mapped[str] = mapped_column(String(256), nullable=False)
    transfer_method: Mapped[str | None] = mapped_column(String(128))  # 原文
    method_category: Mapped[str] = mapped_column(String(16), nullable=False)
    transfer_shares: Mapped[int | None] = mapped_column(BigInteger)
    max_intraday_shares: Mapped[int | None] = mapped_column(BigInteger)
    transferee: Mapped[str | None] = mapped_column(String(256))
    current_own_shares: Mapped[int | None] = mapped_column(BigInteger)
    current_trust_shares: Mapped[int | None] = mapped_column(BigInteger)
    planned_own_shares: Mapped[int | None] = mapped_column(BigInteger)
    planned_trust_shares: Mapped[int | None] = mapped_column(BigInteger)
    after_own_shares: Mapped[int | None] = mapped_column(BigInteger)
    after_trust_shares: Mapped[int | None] = mapped_column(BigInteger)
    effective_start: Mapped[dt.date | None] = mapped_column(Date)
    effective_end: Mapped[dt.date | None] = mapped_column(Date)
    amendment_note: Mapped[str | None] = mapped_column(String(128))  # 異動情形原文
    amends_report_date: Mapped[dt.date | None] = mapped_column(Date)
    superseded_on: Mapped[dt.date | None] = mapped_column(Date)
    unfinished_flag: Mapped[str | None] = mapped_column(String(8))  # 原文，不用於特徵


class MopsFetchCoverage(Base, ProvenanceMixin):
    """某資料集某市場某日（某範圍）已成功抓取（row_count 可為 0，代表官方明確無資料）。

    scope_key：轉讓申報為全市場 `*`；持股網頁為個股代號（data_date = 資料年月月底）。
    fetched_at / source / row_count 記最後一次抓取；ingestion_mode / observed_at 記首次觀測。
    """

    __tablename__ = "mops_fetch_coverage"
    __table_args__ = (
        UniqueConstraint("dataset", "market", "data_date", "scope_key", name="uq_mops_fetch_coverage"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(32), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    data_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    scope_key: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MopsShadowFeatureDaily(Base, AvailabilityMixin, TimestampMixin):
    """Phase 2 shadow 特徵（raw 為自身比率，*_z 為當日橫斷面 z）。正式評分不讀本表。"""

    __tablename__ = "mops_shadow_feature_daily"
    __table_args__ = (
        UniqueConstraint("symbol", "data_date", name="uq_mops_shadow_feature_daily"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(
        String(16), ForeignKey("stock.symbol"), nullable=False, index=True
    )
    holding_month: Mapped[dt.date | None] = mapped_column(Date)  # 使用的最近持股月（月底）
    holding_source: Mapped[str | None] = mapped_column(String(16))
    # point_in_time_safe / backfill_derived / mixed / unknown（honest OOS 只收 point_in_time_safe）
    holding_provenance: Mapped[str] = mapped_column(String(24), nullable=False)
    transfer_provenance: Mapped[str] = mapped_column(String(24), nullable=False)
    insider_holding_change_pct: Mapped[float | None] = mapped_column()
    insider_pledge_ratio: Mapped[float | None] = mapped_column()
    insider_pledge_ratio_change: Mapped[float | None] = mapped_column()
    major_holder_change_pct: Mapped[float | None] = mapped_column()
    transfer_market_sale_ratio: Mapped[float | None] = mapped_column()
    insider_holding_change_z: Mapped[float | None] = mapped_column()
    insider_pledge_ratio_z: Mapped[float | None] = mapped_column()
    insider_pledge_ratio_change_z: Mapped[float | None] = mapped_column()
    major_holder_change_z: Mapped[float | None] = mapped_column()
    transfer_market_sale_z: Mapped[float | None] = mapped_column()
