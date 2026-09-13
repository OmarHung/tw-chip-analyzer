"""Phase 2 MOPS shadow features（docs/14 §6）。**正式評分不讀本模組輸出。**

Look-ahead：一律以 as_of（目標日盤後，台北時間）篩 `available_at <= as_of`；變更申報對舊申報的
取代關係，只在「變更申報本身可用」後才生效。缺資料/視窗不足/任一必要分項未知一律 NULL，
不以 0 冒充中性，也不把不完整的加總當精確值。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Thresholds, get_thresholds
from app.core.logging import get_logger
from app.db.models.market import DailyPrice, Stock
from app.db.models.mops import (
    InsiderHoldingMonthly,
    InsiderTransferDeclaration,
    MopsFetchCoverage,
    MopsShadowFeatureDaily,
)
from app.importers.base import availability_for
from app.importers.mops import (
    DATASET_TRANSFER,
    MODE_BACKFILL,
    MODE_FORWARD,
    SCOPE_ALL,
    SOURCE_OPENAPI,
    TPE,
    transfer_available_at,
)
from app.repositories.upsert import upsert_many
from app.services.feature_builder import _zscore_map

logger = get_logger("services.mops_features")

RAW_TO_Z = {
    "insider_holding_change_pct": "insider_holding_change_z",
    "insider_pledge_ratio": "insider_pledge_ratio_z",
    "insider_pledge_ratio_change": "insider_pledge_ratio_change_z",
    "major_holder_change_pct": "major_holder_change_z",
    "transfer_market_sale_ratio": "transfer_market_sale_z",
}
_MAJOR_PREFIX = "大股東"

# 特徵 provenance（docs/14 §7.1）：honest OOS 只收 PROV_SAFE
PROV_SAFE = "point_in_time_safe"
PROV_BACKFILL = "backfill_derived"
PROV_MIXED = "mixed"
PROV_UNKNOWN = "unknown"
TRANSFER_FACTORS = ("transfer_market_sale_ratio",)


def provenance_column(factor: str) -> str:
    return "transfer_provenance" if factor in TRANSFER_FACTORS else "holding_provenance"


def classify_provenance(modes: Iterable[str | None]) -> str:
    """輸入列的首次觀測模式 → 特徵 provenance。

    全 forward → point_in_time_safe；全 backfill → backfill_derived；forward+backfill → mixed；
    無輸入、或含 unknown/NULL/未知值 → unknown（無法證明是 point-in-time，不進 honest OOS）。
    """
    s = set(modes)
    if not s or not s <= {MODE_FORWARD, MODE_BACKFILL}:
        return PROV_UNKNOWN
    if s == {MODE_FORWARD}:
        return PROV_SAFE
    if s == {MODE_BACKFILL}:
        return PROV_BACKFILL
    return PROV_MIXED


def feature_as_of(target: dt.date) -> dt.datetime:
    """shadow 特徵的資訊截止時點＝正式特徵同一時點（目標日盤後），明確帶台北時區。"""
    return availability_for(target).replace(tzinfo=TPE)


def _month_index(d: dt.date) -> int:
    return d.year * 12 + d.month


# ---------------------------------------------------------------- 持股（純函式）

@dataclass(frozen=True)
class Holder:
    current: int | None
    pledged: int | None
    is_major: bool


def latest_snapshots(rows: list[dict], as_of: dt.datetime) -> dict[str, dict[dt.date, list[dict]]]:
    """{symbol: {month_end: 該月最晚可用的一整組快照列}}。

    同月多版本（source, report_date）只取 available_at 最晚且 <= as_of 的那組，不跨版本混人；
    同時點平手時 OpenAPI 優先（有官方出表日期）。
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["available_at"] <= as_of:
            groups[(r["symbol"], r["data_date"], r["source"], r["report_date"])].append(r)
    best: dict[tuple, tuple] = {}
    for key, grp in groups.items():
        sym, month = key[0], key[1]
        rank = (max(g["available_at"] for g in grp), key[2] == SOURCE_OPENAPI, key[3])
        if (sym, month) not in best or rank > best[(sym, month)][0]:
            best[(sym, month)] = (rank, key)
    out: dict[str, dict[dt.date, list[dict]]] = defaultdict(dict)
    for (sym, month), (_rank, key) in best.items():
        out[sym][month] = groups[key]
    return out


def aggregate_holders(rows: list[dict]) -> dict[str, Holder]:
    """同一人多職稱會重複揭露同樣股數（官方註記勿重複累計）→ 以姓名去重取已知值的最大值。

    該姓名所有列皆未知 → 該人為 None（未知），由後續加總決定整體為 NULL。
    """
    by_name: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_name[r["holder_name"]].append(r)

    def _max(vals):
        known = [v for v in vals if v is not None]
        return max(known) if known else None

    return {
        name: Holder(
            current=_max(g["current_shares"] for g in grp),
            pledged=_max(g["pledged_shares"] for g in grp),
            is_major=any(g["title"].startswith(_MAJOR_PREFIX) for g in grp),
        )
        for name, grp in by_name.items()
    }


def _total(holders: dict[str, Holder]) -> int | None:
    """去重後任一 holder 持股未知 → 合計未知（NULL），不以部分加總冒充全體。"""
    if not holders or any(h.current is None for h in holders.values()):
        return None
    return sum(h.current for h in holders.values())


def _pledge_ratio(holders: dict[str, Holder]) -> float | None:
    """持股 > 0 的 holder 任一設質未知 → NULL；持股合計未知或 ≤ 0 → NULL。"""
    total = _total(holders)
    if total is None or total <= 0:
        return None
    held = [h for h in holders.values() if h.current > 0]
    if any(h.pledged is None for h in held):
        return None
    return sum(h.pledged for h in held) / total


def _continuing_change(cur: dict[str, Holder], prev: dict[str, Holder], major_only: bool) -> float | None:
    """兩個月都出現的同一姓名持股合計變化率；名單進出（改選）不算持股變化。

    共同 holder 任一月份持股未知 → NULL（不排除未知者後繼續算）。
    """
    names = [
        n for n in cur.keys() & prev.keys()
        if not major_only or (cur[n].is_major and prev[n].is_major)
    ]
    if not names or any(cur[n].current is None or prev[n].current is None for n in names):
        return None
    before = sum(prev[n].current for n in names)
    after = sum(cur[n].current for n in names)
    return (after - before) / before if before > 0 else None


@dataclass(frozen=True)
class HoldingFeatures:
    month: dt.date | None
    source: str | None
    total: int | None
    change_pct: float | None
    pledge_ratio: float | None
    pledge_ratio_change: float | None
    major_change_pct: float | None
    current_modes: frozenset[str | None] = frozenset()  # M 月快照（total 的來源）
    provenance: str = PROV_UNKNOWN                     # M 月 + 有用到的 M−1 月


_EMPTY_HOLDING = HoldingFeatures(None, None, None, None, None, None, None)


def holding_features(
    snapshots: dict[dt.date, list[dict]], target: dt.date, max_stale_months: int
) -> HoldingFeatures:
    if not snapshots:
        return _EMPTY_HOLDING
    month = max(snapshots)
    if _month_index(target) - _month_index(month) > max_stale_months:
        return _EMPTY_HOLDING  # 停止申報/下市：不沿用過舊資料
    cur_rows = snapshots[month]
    cur = aggregate_holders(cur_rows)
    prev_month = month.replace(day=1) - dt.timedelta(days=1)
    prev_rows = snapshots.get(prev_month, [])
    prev = aggregate_holders(prev_rows) if prev_rows else None
    ratio = _pledge_ratio(cur)
    prev_ratio = _pledge_ratio(prev) if prev is not None else None
    current_modes = frozenset(r.get("ingestion_mode") for r in cur_rows)
    return HoldingFeatures(
        month=month,
        source=cur_rows[0]["source"],
        total=_total(cur),
        change_pct=_continuing_change(cur, prev, major_only=False) if prev is not None else None,
        pledge_ratio=ratio,
        pledge_ratio_change=(ratio - prev_ratio) if ratio is not None and prev_ratio is not None else None,
        major_change_pct=_continuing_change(cur, prev, major_only=True) if prev is not None else None,
        current_modes=current_modes,
        provenance=classify_provenance(
            [*current_modes, *(r.get("ingestion_mode") for r in prev_rows)]
        ),
    )


# ---------------------------------------------------------------- 轉讓（純函式）

def declaration_total(d: dict) -> tuple[int | None, bool]:
    """單筆申報預定轉讓總股數 → (總數, 是否資料品質異常)。

    fixture 實測 `transfer_shares`（預定轉讓方式及股數-轉讓股數）是**分方式**股數：贈與/信託/
    組合方式時為空白，不能當單筆總數。故總數 = 自有 + 信託，**兩者都已知**才相加；
    `transfer_shares` 只做交叉檢查，已知且與自有+信託不一致 → NULL 並回報異常，不靜默挑一個。
    """
    own, trust = d.get("planned_own_shares"), d.get("planned_trust_shares")
    if own is None or trust is None:
        return None, False
    total = own + trust
    official = d.get("transfer_shares")
    if official is not None and official != total:
        return None, True
    return total, False


@dataclass(frozen=True)
class TransferResolution:
    shares: dict[str, int | None]            # 只含窗內有 market 類有效申報、或需回 NULL 的標的
    modes: dict[str, frozenset[str | None]]  # 標的 → 窗內可見申報（含被取代者）的首次觀測模式
    ambiguous: frozenset[str]                # 變更申報找不到唯一舊申報 → 該檔 NULL
    inconsistent: frozenset[str]             # 官方 total 與自有+信託不一致 → 該檔 NULL


_NO_TRANSFERS = TransferResolution({}, {}, frozenset(), frozenset())


def resolve_transfers(
    declarations: list[dict], as_of: dt.datetime, t: Thresholds | None = None,
    window_start: dt.date | None = None,
) -> TransferResolution:
    """窗內申報 → {symbol: market 類有效申報預定轉讓總股數}。

    1. 只看 `available_at <= as_of`。
    2. **先**解析取代關係（在 method 分類過濾前——變更可能把 market 改成 gift 或反向）：
       - 新申報帶 `amends_report_date` → 找同 symbol、`data_date == amends_report_date`、同申報人
         身分與姓名的舊申報；唯一匹配則剔除舊申報。新申報本身已可用才會被看到，故取代不會提前生效。
         舊申報日早於 window_start → 舊申報本就不在窗內、不會雙算，略過匹配。
         無匹配或多個候選 → 該檔 ambiguous → NULL（不可新舊雙算）。
       - 舊申報帶 `superseded_on`（回補頁事後回寫）→ 只在該日揭露時點後剔除（保留原規則）。
    3. 剩下的 market 類申報加總；任一筆總數未知或異常 → 該檔 NULL。
    """
    visible = [d for d in declarations if d["available_at"] <= as_of]
    by_target: dict[tuple, list[int]] = defaultdict(list)
    for i, d in enumerate(visible):
        by_target[(d["symbol"], d["data_date"], d["reporter_role"], d["reporter_name"])].append(i)

    dropped: set[int] = set()
    ambiguous: set[str] = set()
    for i, new in enumerate(visible):
        target = new.get("amends_report_date")
        if target is None or (window_start is not None and target < window_start):
            continue
        cands = [j for j in by_target.get(
            (new["symbol"], target, new["reporter_role"], new["reporter_name"]), []) if j != i]
        if len(cands) == 1:
            dropped.add(cands[0])
        else:
            ambiguous.add(new["symbol"])
    for j, d in enumerate(visible):
        sup = d.get("superseded_on")
        if sup is not None and transfer_available_at(sup, t) <= as_of:
            dropped.add(j)

    modes: dict[str, set] = defaultdict(set)
    shares: dict[str, int | None] = {}
    inconsistent: set[str] = set()
    for j, d in enumerate(visible):
        sym = d["symbol"]
        modes[sym].add(d.get("ingestion_mode"))
        if j in dropped or d["method_category"] != "market":
            continue
        total, bad = declaration_total(d)
        if bad:
            inconsistent.add(sym)
        if sym in shares and shares[sym] is None:
            continue
        shares[sym] = None if total is None else shares.get(sym, 0) + total
    for sym in ambiguous | inconsistent:
        shares[sym] = None
    return TransferResolution(
        shares=shares,
        modes={s: frozenset(v) for s, v in modes.items()},
        ambiguous=frozenset(ambiguous),
        inconsistent=frozenset(inconsistent),
    )


def transfer_sale_shares(
    declarations: list[dict], as_of: dt.datetime, t: Thresholds | None = None,
    window_start: dt.date | None = None,
) -> dict[str, int | None]:
    """{symbol: 窗內 market 類有效申報預定轉讓總股數}；未知/歧義 → None。見 `resolve_transfers`。"""
    return resolve_transfers(declarations, as_of, t, window_start).shares


def transfer_ratio(
    symbol: str, sale: dict[str, int | None], total: int | None, covered: bool
) -> float | None:
    if not covered or total is None or total <= 0:
        return None
    if symbol not in sale:
        return 0.0  # 窗內每日都有涵蓋且確實沒有 market 類有效申報 → 真的 0
    shares = sale[symbol]
    return None if shares is None else shares / total


# ---------------------------------------------------------------- DB 建構

@dataclass(frozen=True)
class MopsFeatureBuild:
    rows: int
    amendment_ambiguous: tuple[str, ...] = ()
    transfer_inconsistent: tuple[str, ...] = ()


async def _window_dates(session: AsyncSession, target: dt.date, n: int, as_of: dt.datetime,
                        t: Thresholds) -> list[dt.date]:
    """最近 n 個「申報已可用」的市場交易日（交易日曆取自 daily_price）；不足 n 天 → []。"""
    dates = (await session.execute(
        select(distinct(DailyPrice.data_date))
        .where(DailyPrice.data_date <= target)
        .order_by(DailyPrice.data_date.desc())
        .limit(n + 5)
    )).scalars().all()
    usable = [d for d in dates if transfer_available_at(d, t) <= as_of][:n]
    return sorted(usable) if len(usable) == n else []


_HOLDING_COLS = ["symbol", "data_date", "source", "report_date", "available_at", "title",
                 "holder_name", "current_shares", "pledged_shares", "ingestion_mode"]
_DECL_COLS = ["symbol", "row_hash", "data_date", "available_at", "reporter_role", "reporter_name",
              "transfer_method", "method_category", "transfer_shares", "planned_own_shares",
              "planned_trust_shares", "amends_report_date", "superseded_on", "source",
              "ingestion_mode"]


async def _load_holdings(session: AsyncSession, symbols: list[str], target: dt.date,
                         as_of: dt.datetime, max_stale: int) -> dict:
    first = dt.date(target.year, target.month, 1)
    for _ in range(max_stale + 1):
        first = (first - dt.timedelta(days=1)).replace(day=1)
    rows = [dict(zip(_HOLDING_COLS, r)) for r in (await session.execute(
        select(*[getattr(InsiderHoldingMonthly, c) for c in _HOLDING_COLS]).where(
            InsiderHoldingMonthly.symbol.in_(symbols),
            InsiderHoldingMonthly.data_date >= first,
            InsiderHoldingMonthly.data_date <= target,
            InsiderHoldingMonthly.available_at <= as_of,
        )
    )).all()]
    return latest_snapshots(rows, as_of)


async def _load_transfer_window(
    session: AsyncSession, symbols: list[str], window: list[dt.date], target: dt.date,
    as_of: dt.datetime,
) -> tuple[dict[str, frozenset], list[dict]]:
    """({market: 涵蓋模式}（只含窗內每日都有涵蓋的市場）, 窗內申報)。"""
    cov = (await session.execute(
        select(MopsFetchCoverage.market, MopsFetchCoverage.data_date,
               MopsFetchCoverage.ingestion_mode).where(
            MopsFetchCoverage.dataset == DATASET_TRANSFER,
            MopsFetchCoverage.scope_key == SCOPE_ALL,
            MopsFetchCoverage.data_date.in_(window),
        )
    )).all()
    days: dict[str, set[dt.date]] = defaultdict(set)
    cov_modes: dict[str, set] = defaultdict(set)
    for mk, d, mode in cov:
        days[mk].add(d)
        cov_modes[mk].add(mode)
    covered = {mk: frozenset(cov_modes[mk]) for mk, ds in days.items() if ds >= set(window)}
    decls = [dict(zip(_DECL_COLS, r)) for r in (await session.execute(
        select(*[getattr(InsiderTransferDeclaration, c) for c in _DECL_COLS]).where(
            InsiderTransferDeclaration.symbol.in_(symbols),
            InsiderTransferDeclaration.data_date >= window[0],
            InsiderTransferDeclaration.data_date <= target,
            InsiderTransferDeclaration.available_at <= as_of,
        )
    )).all()]
    return covered, decls


async def build_mops_features(
    session: AsyncSession, target: dt.date, thresholds: Thresholds | None = None
) -> MopsFeatureBuild:
    t = thresholds or get_thresholds()
    as_of = feature_as_of(target)
    hcfg = t.get("mops", "insider_holding", default={}) or {}
    tcfg = t.get("mops", "transfer_declaration", default={}) or {}
    max_stale = int(hcfg.get("max_stale_months", 2))

    markets = dict((await session.execute(
        select(Stock.symbol, Stock.market)
        .join(DailyPrice, DailyPrice.symbol == Stock.symbol)
        .where(DailyPrice.data_date == target)
    )).all())
    if not markets:
        return MopsFeatureBuild(0)
    symbols = list(markets)
    snaps = await _load_holdings(session, symbols, target, as_of, max_stale)

    window = await _window_dates(session, target, int(tcfg.get("window_bars", 20)), as_of, t)
    covered: dict[str, frozenset] = {}
    resolution = _NO_TRANSFERS
    if window:
        covered, decls = await _load_transfer_window(session, symbols, window, target, as_of)
        resolution = resolve_transfers(decls, as_of, t, window_start=window[0])
    if resolution.ambiguous or resolution.inconsistent:
        logger.warning("MOPS %s 轉讓特徵 NULL：變更申報無法唯一匹配 %s；總股數不一致 %s",
                       target, sorted(resolution.ambiguous), sorted(resolution.inconsistent))

    raw: dict[str, dict] = {}
    for sym in symbols:
        hf = holding_features(snaps.get(sym, {}), target, max_stale)
        mk = markets[sym]
        # 轉讓特徵的輸入 = 窗內涵蓋 + 窗內申報 + 分母（M 月持股）；涵蓋不完整或無分母 → unknown
        transfer_modes = [*covered.get(mk, {None}), *resolution.modes.get(sym, ()),
                          *(hf.current_modes or {None})]
        raw[sym] = {
            "holding_month": hf.month,
            "holding_source": hf.source,
            "holding_provenance": hf.provenance,
            "transfer_provenance": classify_provenance(transfer_modes),
            "insider_holding_change_pct": hf.change_pct,
            "insider_pledge_ratio": hf.pledge_ratio,
            "insider_pledge_ratio_change": hf.pledge_ratio_change,
            "major_holder_change_pct": hf.major_change_pct,
            "transfer_market_sale_ratio": transfer_ratio(
                sym, resolution.shares, hf.total, mk in covered
            ),
        }

    zmaps = {
        z_col: _zscore_map({s: v[col] for s, v in raw.items() if v[col] is not None})
        for col, z_col in RAW_TO_Z.items()
    }
    rows = [
        {"symbol": sym, "data_date": target, "available_at": as_of, **vals,
         **{z_col: zmaps[z_col].get(sym) for z_col in RAW_TO_Z.values()}}
        for sym, vals in raw.items()
    ]
    n = await upsert_many(session, MopsShadowFeatureDaily, rows, ["symbol", "data_date"])
    await session.commit()
    return MopsFeatureBuild(
        rows=n,
        amendment_ambiguous=tuple(sorted(resolution.ambiguous)),
        transfer_inconsistent=tuple(sorted(resolution.inconsistent)),
    )
