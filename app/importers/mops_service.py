"""MOPS Phase 2 匯入服務：解析後的列 → DB（冪等 upsert，含涵蓋紀錄與 provenance）。見 docs/14。

Provenance（docs/14 §7.1）：每次匯入必須明確帶 `mode`（forward / backfill）。raw 列與涵蓋紀錄的
`ingestion_mode` / `observed_at` 記「首次觀測」，衝突更新時一律不覆寫——重匯或換來源不會把
backfill 洗成 forward，也不會把 forward 洗成 backfill。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.market import Stock
from app.db.models.mops import InsiderHoldingMonthly, InsiderTransferDeclaration, MopsFetchCoverage
from app.importers.mops import (
    DATASET_HOLDING,
    DATASET_TRANSFER,
    MARKET_TPEX,
    MARKET_TWSE,
    MODE_BACKFILL,
    MODE_FORWARD,
    SCOPE_ALL,
    SOURCE_WEB,
    TPE,
    MopsPageError,
    TransferBatch,
)
from app.repositories.upsert import upsert_ignore, upsert_many

_HOLDING_KEY = ["symbol", "data_date", "source", "report_date", "title", "holder_name", "row_seq"]
_TRANSFER_KEY = ["symbol", "data_date", "row_hash"]
_COVERAGE_KEY = ["dataset", "market", "data_date", "scope_key"]
_PROVENANCE = ("ingestion_mode", "observed_at")
# 網頁回補才有的註記（會被官方事後回寫）；OpenAPI 無此欄，重匯時不可把它洗成 NULL
_TRANSFER_ANNOTATIONS = [
    "amendment_note", "amends_report_date", "superseded_on", "unfinished_flag", "duplicate_count",
]


def _check(market: str, mode: str) -> None:
    if market not in (MARKET_TWSE, MARKET_TPEX):
        raise ValueError(f"MOPS market 必須是 {MARKET_TWSE}/{MARKET_TPEX}（同 stock.market）：{market!r}")
    if mode not in (MODE_FORWARD, MODE_BACKFILL):
        raise ValueError(f"ingestion_mode 必須是 {MODE_FORWARD}/{MODE_BACKFILL}：{mode!r}")


def _stamp(rows: list[dict], mode: str, observed_at: dt.datetime) -> list[dict]:
    for mk in {r["market"] for r in rows}:
        _check(mk, mode)
    return [{**r, "ingestion_mode": mode, "observed_at": observed_at} for r in rows]


async def _ensure_stocks(session: AsyncSession, rows: list[dict]) -> None:
    """為 FK 補主檔 stub（不覆蓋既有）；市場別取自來源，不預設成上市。"""
    stubs = {r["symbol"]: {"symbol": r["symbol"], "name": r["symbol"], "market": r["market"]}
             for r in rows}
    await upsert_ignore(session, Stock, list(stubs.values()), ["symbol"])


async def _record_coverage(
    session: AsyncSession, *, dataset: str, market: str, data_date: dt.date, scope_key: str,
    source: str, row_count: int, mode: str, observed_at: dt.datetime,
) -> None:
    _check(market, mode)
    await upsert_many(
        session, MopsFetchCoverage,
        [{"dataset": dataset, "market": market, "data_date": data_date, "scope_key": scope_key,
          "source": source, "row_count": row_count, "fetched_at": observed_at,
          "ingestion_mode": mode, "observed_at": observed_at}],
        _COVERAGE_KEY, update_columns=["source", "row_count", "fetched_at"],
    )


async def _upsert_holdings(session: AsyncSession, rows: list[dict], mode: str,
                           observed_at: dt.datetime) -> int:
    if not rows:
        return 0
    stamped = _stamp(rows, mode, observed_at)
    await _ensure_stocks(session, stamped)
    update = [c for c in stamped[0] if c not in _HOLDING_KEY and c not in _PROVENANCE]
    return await upsert_many(session, InsiderHoldingMonthly, stamped, _HOLDING_KEY,
                             update_columns=update)


async def import_holdings(session: AsyncSession, rows: list[dict], *, mode: str,
                          observed_at: dt.datetime | None = None) -> int:
    """持股列 upsert。同 (symbol, 月, source, report_date, 職稱, 姓名, row_seq) 重匯覆寫為同值（冪等）；
    不同 report_date 的修正版另存新列，舊版不動（docs/14 §2.4）。"""
    n = await _upsert_holdings(session, rows, mode, observed_at or dt.datetime.now(TPE))
    await session.commit()
    return n


async def import_holdings_page(
    session: AsyncSession, rows: list[dict], *, symbol: str, market: str, month_end: dt.date,
    mode: str, observed_at: dt.datetime | None = None,
) -> int:
    """單公司單月網頁（已成功解析）→ 持股列 + 涵蓋紀錄（零筆頁也記，回補才不會每次重抓）。

    回應的公司/資料年月與查詢不符 → MopsPageError，且不寫任何東西（不可把異常頁當成涵蓋）。
    """
    bad = [r for r in rows if r["symbol"] != symbol or r["data_date"] != month_end]
    if bad:
        raise MopsPageError(
            f"回應 {bad[0]['symbol']} {bad[0]['data_date']} ≠ 查詢 {symbol} {month_end}"
        )
    obs = observed_at or dt.datetime.now(TPE)
    n = await _upsert_holdings(session, rows, mode, obs)
    await _record_coverage(
        session, dataset=DATASET_HOLDING, market=market, data_date=month_end, scope_key=symbol,
        source=SOURCE_WEB, row_count=len(rows), mode=mode, observed_at=obs,
    )
    await session.commit()
    return n


async def import_transfers(session: AsyncSession, batch: TransferBatch, market: str,
                           source: str, *, mode: str,
                           observed_at: dt.datetime | None = None) -> int:
    """轉讓申報 upsert + 寫入涵蓋紀錄（零筆日也寫，代表官方明確無申報）。

    - 網頁來源：衝突時只更新註記欄（異動情形/變更關係/重複次數），核心欄位由 row_hash 固定。
    - OpenAPI 來源：衝突時不更新（不把網頁才有的註記洗掉）。
    - 兩者都不更新 provenance（首次觀測）。
    """
    _check(market, mode)
    obs = observed_at or dt.datetime.now(TPE)
    rows = _stamp(batch.rows, mode, obs)
    if rows:
        await _ensure_stocks(session, rows)
        if source == SOURCE_WEB:
            await upsert_many(session, InsiderTransferDeclaration, rows, _TRANSFER_KEY,
                              update_columns=_TRANSFER_ANNOTATIONS)
        else:
            await upsert_ignore(session, InsiderTransferDeclaration, rows, _TRANSFER_KEY)
    if batch.report_date is not None:
        await _record_coverage(
            session, dataset=DATASET_TRANSFER, market=market, data_date=batch.report_date,
            scope_key=SCOPE_ALL, source=source, row_count=len(rows), mode=mode, observed_at=obs,
        )
    await session.commit()
    return len(rows)
