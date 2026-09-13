"""Phase 2 MOPS 審查修正（docs/15）：amendment 取代、嚴格 NULL、provenance / honest OOS、
持股空頁涵蓋冪等、排程安全，以及端到端 shadow-only 不變性。"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.core.config import get_thresholds, load_yaml_thresholds
from app.core.threshold_registry import data_version
from app.db.models.features import FeatureDaily, SignalSnapshot
from app.db.models.market import Stock
from app.db.models.mops import (
    InsiderHoldingMonthly,
    InsiderTransferDeclaration,
    MopsFetchCoverage,
    MopsShadowFeatureDaily,
)
from app.importers import mops as m
from app.importers.mops_service import import_holdings, import_holdings_page, import_transfers
from app.services.feature_builder import build_features
from app.services.mops_features import (
    PROV_BACKFILL,
    PROV_MIXED,
    PROV_SAFE,
    PROV_UNKNOWN,
    aggregate_holders,
    build_mops_features,
    classify_provenance,
    declaration_total,
    holding_features,
    resolve_transfers,
    transfer_sale_shares,
)
from app.services.signal_persist import persist_signals
from scripts.mops_factor_oos import (
    VERDICT_INSUFFICIENT,
    VERDICT_RETRO,
    VERDICT_ROBUST,
    honest_values,
    provenance_counts,
    verdict,
)
from tests.test_mops_features_db import (
    DEC,
    JAN,
    _cover_all,
    _decl,
    _feature,
    _feature_state,
    _hold,
    _snapshot_state,
)
from tests.test_signal_persist import TARGET, _seed_history

FX = Path(__file__).parent / "fixtures"
TPE = m.TPE
FWD, BF = m.MODE_FORWARD, m.MODE_BACKFILL
D10, D12 = dt.date(2026, 3, 10), dt.date(2026, 3, 12)
AS_OF = dt.datetime(2026, 3, 20, 15, tzinfo=TPE)


def _amend(sym: str, day: dt.date, planned: int | None, amends: dt.date, **kw) -> dict:
    row = _decl(sym, day, planned, **kw)
    return {**row, "amends_report_date": amends, "amendment_note": f"本單為變更{amends} 之申報"}


# ---------------------------------------------------------------- P1-2 forward amendment

def test_amendment_replaces_old_only_after_available():
    old, new = _decl("AAA", D10, 1000), _amend("AAA", D12, 600, D10)
    assert transfer_sale_shares([old, new], AS_OF) == {"AAA": 600}  # 不是 1,600
    before_new = dt.datetime(2026, 3, 12, 15, tzinfo=TPE)  # 03-12 申報 → 03-13 08:00 才可用
    assert transfer_sale_shares([old, new], before_new) == {"AAA": 1000}


def test_amendment_market_to_gift_drops_old_and_gift_not_counted():
    old, new = _decl("AAA", D10, 1000), _amend("AAA", D12, 1000, D10, method="贈與")
    assert transfer_sale_shares([old, new], AS_OF) == {}


def test_amendment_gift_to_market_counts_only_new():
    old, new = _decl("AAA", D10, 1000, method="贈與"), _amend("AAA", D12, 700, D10)
    assert transfer_sale_shares([old, new], AS_OF) == {"AAA": 700}


def test_ambiguous_amendment_is_null_not_double_counted():
    no_match = _amend("AAA", D12, 600, D10)  # 窗內找不到舊申報
    two_old = [_decl("BBB", D10, 1000), _decl("BBB", D10, 500, method="盤後定價交易")]
    got = resolve_transfers([no_match, *two_old, _amend("BBB", D12, 600, D10)], AS_OF,
                            window_start=dt.date(2026, 2, 28))
    assert got.shares == {"AAA": None, "BBB": None}
    assert got.ambiguous == {"AAA", "BBB"}


def test_amendment_of_declaration_before_window_is_not_ambiguous():
    new = _amend("AAA", D12, 600, dt.date(2026, 2, 1))
    got = resolve_transfers([new], AS_OF, window_start=dt.date(2026, 2, 28))
    assert got.shares == {"AAA": 600} and not got.ambiguous


def test_superseded_on_only_backfill_case_still_works():
    old = _decl("AAA", D10, 1000, superseded_on=D12)
    assert transfer_sale_shares([old], AS_OF) == {}
    assert transfer_sale_shares([old], dt.datetime(2026, 3, 12, 15, tzinfo=TPE)) == {"AAA": 1000}


def test_declarations_without_amendment_unchanged():
    decls = [_decl("AAA", D10, 1000), _decl("AAA", D12, 500, name="乙"), _decl("BBB", D10, 300)]
    assert transfer_sale_shares(decls, AS_OF) == {"AAA": 1500, "BBB": 300}


def test_fixture_amendment_chain_2442():
    old = m.parse_transfer_page((FX / "mops_t56sb12_SY_1150814.html").read_text("utf-8"),
                                "TWSE", dt.date(2026, 8, 14)).rows
    new = m.parse_transfer_page((FX / "mops_t56sb12_SY_1150818.html").read_text("utf-8"),
                                "TWSE", dt.date(2026, 8, 18)).rows
    # 模擬 forward：舊頁抓取當時還沒有回寫的「已變更」註記
    old_forward = [{**r, "superseded_on": None} for r in old if r["symbol"] == "2442"]
    new_2442 = [r for r in new if r["symbol"] == "2442"]
    as_of = dt.datetime(2026, 8, 20, 15, tzinfo=TPE)
    got = transfer_sale_shares([*old_forward, *new_2442], as_of)
    assert got == {"2442": 948_530 + 243_844}  # 未成年子女那筆以新申報 243,844 取代，不雙算


# ---------------------------------------------------------------- P1-4 嚴格 NULL

def _h(name: str, cur, pledged=0, title="董事本人") -> dict:
    return {"holder_name": name, "current_shares": cur, "pledged_shares": pledged, "title": title,
            "source": m.SOURCE_OPENAPI, "ingestion_mode": FWD}


def test_pledge_ratio_null_when_any_positive_holder_pledge_unknown():
    hf = holding_features({JAN: [_h("甲", 1000, 100), _h("乙", 1000, None)]}, TARGET, 2)
    assert hf.pledge_ratio is None and hf.total == 2000


def test_total_null_when_any_holder_current_unknown():
    hf = holding_features({JAN: [_h("甲", 1000), _h("乙", None)]}, TARGET, 2)
    assert hf.total is None and hf.pledge_ratio is None


def test_duplicate_titles_dedupe_with_known_value():
    holders = aggregate_holders([_h("甲", 1000), _h("甲", None, title="大股東本人")])
    assert holders["甲"].current == 1000


def test_change_null_when_continuing_holder_unknown_in_either_month():
    cur_unknown = {DEC: [_h("甲", 1000), _h("乙", 500)], JAN: [_h("甲", 1100), _h("乙", None)]}
    prev_unknown = {DEC: [_h("甲", 1000), _h("乙", None)], JAN: [_h("甲", 1100), _h("乙", 500)]}
    assert holding_features(cur_unknown, TARGET, 2).change_pct is None
    assert holding_features(prev_unknown, TARGET, 2).change_pct is None
    roster = {DEC: [_h("甲", 1000)], JAN: [_h("甲", 1100), _h("新任", None)]}
    assert holding_features(roster, TARGET, 2).change_pct == pytest.approx(0.1)  # 新進者不算變化


def test_transfer_total_requires_both_parts_and_consistent_official_total():
    base = _decl("AAA", D10, 500)
    assert declaration_total({**base, "planned_trust_shares": None, "transfer_shares": None}) == (None, False)
    assert declaration_total({**base, "planned_trust_shares": 0}) == (500, False)
    assert declaration_total({**base, "transfer_shares": 800}) == (None, True)
    got = resolve_transfers([{**base, "transfer_shares": 800}], AS_OF)
    assert got.shares == {"AAA": None} and got.inconsistent == {"AAA"}
    partial = {**base, "planned_trust_shares": None, "transfer_shares": None}
    assert transfer_sale_shares([partial, _decl("AAA", D12, 100, name="乙")], AS_OF) == {"AAA": None}


def test_complete_holding_data_unchanged():
    hf = holding_features({DEC: [_h("甲", 1000), _h("乙", 1000)],
                           JAN: [_h("甲", 1200, 600), _h("乙", 1000, 0)]}, TARGET, 2)
    assert hf.total == 2200 and hf.change_pct == pytest.approx(0.1)
    assert hf.pledge_ratio == pytest.approx(600 / 2200)


# ---------------------------------------------------------------- P1-3 provenance

def test_classify_provenance():
    assert classify_provenance([FWD, FWD]) == PROV_SAFE
    assert classify_provenance([BF]) == PROV_BACKFILL
    assert classify_provenance([FWD, BF]) == PROV_MIXED
    assert classify_provenance([FWD, m.MODE_UNKNOWN]) == PROV_UNKNOWN
    assert classify_provenance([FWD, None]) == PROV_UNKNOWN
    assert classify_provenance([]) == PROV_UNKNOWN


async def test_backfill_holdings_excluded_forward_included_mixed_excluded(db_session):
    await _seed_history(db_session)
    web = m.SOURCE_WEB
    await import_holdings(db_session, [_hold("AAA", mo, "甲", 1000) for mo in (DEC, JAN)], mode=FWD)
    await import_holdings(db_session, [_hold("BBB", mo, "甲", 1000, source=web) for mo in (DEC, JAN)],
                          mode=BF)
    await import_holdings(db_session, [_hold("CCC", DEC, "甲", 1000, source=web)], mode=BF)
    await import_holdings(db_session, [_hold("CCC", JAN, "甲", 1000)], mode=FWD)
    await build_mops_features(db_session, TARGET)
    got = {s: (await _feature(db_session, s)).holding_provenance for s in ("AAA", "BBB", "CCC")}
    assert got == {"AAA": PROV_SAFE, "BBB": PROV_BACKFILL, "CCC": PROV_MIXED}

    rows = (await db_session.execute(select(MopsShadowFeatureDaily))).scalars().all()
    df = pd.DataFrame([{c: getattr(r, c) for c in ("symbol", "data_date", "holding_provenance",
                                                   "transfer_provenance", "insider_pledge_ratio")}
                       for r in rows])
    kept = df.loc[honest_values(df, "insider_pledge_ratio").notna(), "symbol"].tolist()
    assert kept == ["AAA"]
    counts = provenance_counts(df, "insider_pledge_ratio", min_names=1)
    assert counts == {"safe": 1, "backfill": 1, "mixed_unknown": 1, "honest_days": 1}


async def test_backfill_transfer_coverage_not_relabelled_forward(db_session):
    await _seed_history(db_session)
    batch = m.TransferBatch(D10, [_decl("AAA", D10, 500)])
    await import_transfers(db_session, batch, "TWSE", m.SOURCE_WEB, mode=BF)
    # forward 重匯（網頁與 OpenAPI 都試）不可把首次觀測洗成 forward
    await import_transfers(db_session, batch, "TWSE", m.SOURCE_WEB, mode=FWD)
    api = m.TransferBatch(D10, [{**batch.rows[0], "source": m.SOURCE_OPENAPI}])
    await import_transfers(db_session, api, "TWSE", m.SOURCE_OPENAPI, mode=FWD)
    db_session.expunge_all()
    (cov,) = (await db_session.execute(select(MopsFetchCoverage))).scalars().all()
    (decl,) = (await db_session.execute(select(InsiderTransferDeclaration))).scalars().all()
    assert cov.ingestion_mode == BF and decl.ingestion_mode == BF
    assert cov.source == m.SOURCE_OPENAPI  # 最後一次抓取資訊照常更新


async def test_holding_provenance_not_overwritten_by_reimport(db_session):
    await _seed_history(db_session)
    rows = [_hold("AAA", JAN, "甲", 1000)]
    first = dt.datetime(2026, 2, 22, 9, tzinfo=TPE)
    await import_holdings(db_session, rows, mode=FWD, observed_at=first)
    await import_holdings(db_session, rows, mode=BF, observed_at=first + dt.timedelta(days=30))
    db_session.expunge_all()
    (r,) = (await db_session.execute(select(InsiderHoldingMonthly))).scalars().all()
    assert (r.ingestion_mode, r.observed_at) == (FWD, first)


async def test_transfer_provenance_combines_coverage_declarations_and_denominator(db_session):
    await _seed_history(db_session)
    await import_holdings(db_session, [_hold(s, mo, "甲", 1000) for mo in (DEC, JAN) for s in ("AAA", "BBB")],
                          mode=FWD)
    await _cover_all(db_session)  # forward 涵蓋
    await import_transfers(db_session, m.TransferBatch(D12, [_decl("BBB", D12, 100)]), "TWSE",
                           m.SOURCE_WEB, mode=BF)
    await build_mops_features(db_session, TARGET)
    assert (await _feature(db_session, "AAA")).transfer_provenance == PROV_SAFE
    assert (await _feature(db_session, "BBB")).transfer_provenance == PROV_MIXED
    assert (await _feature(db_session, "CCC")).transfer_provenance == PROV_UNKNOWN  # 無持股分母


async def test_import_rejects_non_canonical_market_or_mode(db_session):
    with pytest.raises(ValueError):
        await import_transfers(db_session, m.TransferBatch(D10, []), "TPEX", m.SOURCE_WEB, mode=FWD)
    with pytest.raises(ValueError):
        await import_transfers(db_session, m.TransferBatch(D10, []), "TWSE", m.SOURCE_WEB, mode="unknown")


def test_verdict_robust_only_for_honest_with_enough_days():
    strong_tr = {"ic": 0.05, "t_nw": 3.0, "n_days": 40}
    strong_te = {"ic": 0.05, "t_nw": 3.5, "n_days": 40}
    assert verdict(strong_tr, strong_te, honest=False, min_test_days=20) == VERDICT_RETRO
    assert verdict(strong_tr, strong_te, honest=True, min_test_days=20) == VERDICT_ROBUST
    few = {**strong_te, "n_days": 5}
    assert verdict(strong_tr, few, honest=True, min_test_days=20) == VERDICT_INSUFFICIENT
    empty = {"ic": None, "t_nw": None, "n_days": 0}
    assert verdict(empty, empty, honest=True, min_test_days=20) == VERDICT_INSUFFICIENT


# ---------------------------------------------------------------- P2 持股空頁涵蓋冪等

@pytest.fixture
def backfill(monkeypatch):
    import scripts.backfill_mops_holdings as bf

    calls: list[tuple[str, int]] = []
    pages: dict[str, object] = {}

    async def fake_fetch(sym, mk, year, month):
        calls.append((sym, month))
        page = pages[sym]
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(bf.conn, "fetch_holdings_page", fake_fetch)
    monkeypatch.setattr(bf.conn, "throttle_sec", lambda: 0.0)
    monkeypatch.setattr(bf, "available_months", lambda today, months: [dt.date(2026, 7, 31)])
    return bf, calls, pages


async def _holding_cov(session) -> list[MopsFetchCoverage]:
    session.expunge_all()
    return (await session.execute(select(MopsFetchCoverage).where(
        MopsFetchCoverage.dataset == m.DATASET_HOLDING))).scalars().all()


async def test_empty_holding_page_recorded_and_skipped_next_time(db_session, backfill):
    bf, calls, pages = backfill
    await _seed_history(db_session)
    pages["AAA"] = (FX / "mops_stapap1_sii_6919_10001_nodata.html").read_text("utf-8")  # 官方實測零筆頁
    await bf.run(10, 1, ["AAA"], dry_run=False)
    (cov,) = await _holding_cov(db_session)
    assert (cov.scope_key, cov.row_count, cov.ingestion_mode, cov.source) == ("AAA", 0, BF, m.SOURCE_WEB)
    await bf.run(10, 1, ["AAA"], dry_run=False)
    assert calls == [("AAA", 7)]  # 第二次略過，不再請求


async def test_holding_page_with_rows_idempotent(db_session, backfill):
    bf, calls, pages = backfill
    db_session.add(Stock(symbol="2330", name="台積電", market="TWSE"))
    await db_session.commit()
    pages["2330"] = (FX / "mops_stapap1_sii_2330_11507.html").read_text("utf-8")
    await bf.run(10, 1, ["2330"], dry_run=False)
    n = (await db_session.execute(select(func.count()).select_from(InsiderHoldingMonthly))).scalar()
    assert n > 0
    (cov,) = await _holding_cov(db_session)
    assert cov.row_count == n
    await bf.run(10, 1, ["2330"], dry_run=False)
    assert len(calls) == 1
    # 直接重匯同一頁也冪等
    rows = m.parse_holdings_page(pages["2330"], "2330", "TWSE")
    await import_holdings_page(db_session, rows, symbol="2330", market="TWSE",
                               month_end=dt.date(2026, 7, 31), mode=BF)
    assert (await db_session.execute(select(func.count()).select_from(InsiderHoldingMonthly))).scalar() == n
    assert len(await _holding_cov(db_session)) == 1


async def test_parser_error_and_mismatched_month_write_no_coverage(db_session, backfill):
    bf, _calls, pages = backfill
    await _seed_history(db_session)
    pages["AAA"] = "<table><tr class='odd'><td>a</td></tr></table>"  # 有列無資料年月
    pages["BBB"] = (FX / "mops_stapap1_sii_2330_11507.html").read_text("utf-8").replace(
        "11507", "11506").replace("2330", "BBB")  # 回應的資料年月與查詢不符
    pages["CCC"] = (FX / "mops_stapap1_sii_2330_11507.html").read_text("utf-8")  # 回應別家公司
    await bf.run(10, 1, ["AAA", "BBB", "CCC"], dry_run=False)
    assert await _holding_cov(db_session) == []
    assert (await db_session.execute(select(func.count()).select_from(InsiderHoldingMonthly))).scalar() == 0


@pytest.mark.parametrize("page", [
    "<html>系統忙碌，請稍後再試</html>",
    "<html></html>",
    "查無此公司資料",
])
async def test_busy_or_blank_page_writes_no_coverage_and_retries(db_session, backfill, page):
    bf, calls, pages = backfill
    await _seed_history(db_session)
    pages["AAA"] = page if page != "查無此公司資料" else (
        FX / "mops_stapap1_sii_9999_11507_nocompany.html").read_text("utf-8")
    await bf.run(10, 1, ["AAA"], dry_run=False)
    assert await _holding_cov(db_session) == []
    await bf.run(10, 1, ["AAA"], dry_run=False)
    assert calls == [("AAA", 7), ("AAA", 7)]  # 未寫涵蓋 → 下次回補重試


async def test_blocked_response_writes_no_coverage(db_session, backfill):
    bf, _calls, pages = backfill
    await _seed_history(db_session)
    pages["AAA"] = bf.conn.MopsBlockedError("因為安全性考量")
    with pytest.raises(bf.conn.MopsBlockedError):
        await bf.run(10, 1, ["AAA"], dry_run=False)
    assert await _holding_cov(db_session) == []


async def test_transfer_and_holding_coverage_coexist_same_day(db_session):
    await _seed_history(db_session)
    day = dt.date(2026, 7, 31)
    await import_transfers(db_session, m.TransferBatch(day, []), "TWSE", m.SOURCE_WEB, mode=FWD)
    await import_holdings_page(db_session, [], symbol="AAA", market="TWSE", month_end=day, mode=BF)
    await import_holdings_page(db_session, [], symbol="BBB", market="TWSE", month_end=day, mode=BF)
    db_session.expunge_all()
    keys = sorted((c.dataset, c.scope_key) for c in
                  (await db_session.execute(select(MopsFetchCoverage))).scalars().all())
    assert keys == [(m.DATASET_HOLDING, "AAA"), (m.DATASET_HOLDING, "BBB"), (m.DATASET_TRANSFER, m.SCOPE_ALL)]


# ---------------------------------------------------------------- 排程安全

def test_mops_schedule_disabled_in_config_is_not_scheduled(monkeypatch):
    import app.jobs.scheduler as scheduler_mod
    from app.core.config import Thresholds

    raw = load_yaml_thresholds()
    raw["mops"]["schedule"]["enabled"] = False
    monkeypatch.setattr(scheduler_mod, "get_thresholds", lambda: Thresholds(raw))
    sch = scheduler_mod._build_scheduler()
    assert sch.get_job("mops") is None and sch.get_job("eod") is not None


def test_local_today_uses_configured_timezone():
    from app.jobs.mops import local_today

    tz = get_thresholds().schedule.get("timezone", "Asia/Taipei")
    assert local_today() == dt.datetime.now(ZoneInfo(tz)).date()


# ---------------------------------------------------------------- 端到端整合

async def _raw_counts(session) -> tuple[int, int, int]:
    return tuple([(await session.execute(select(func.count()).select_from(t))).scalar()
                  for t in (InsiderHoldingMonthly, InsiderTransferDeclaration, MopsFetchCoverage)])


async def _import_all(session) -> None:
    await import_holdings(session, [_hold(s, mo, "甲", 1000) for mo in (DEC, JAN) for s in ("AAA",)],
                          mode=FWD)
    await import_holdings(session, [{**_hold("BBB", mo, n, 500), "market": m.MARKET_TPEX}
                                    for mo in (DEC, JAN) for n in ("甲", "乙")], mode=FWD)
    # CCC：網頁回補，且一位持股 > 0 的 holder 設質未知（部分 NULL）
    await import_holdings(session, [_hold("CCC", mo, n, 1000, pledged=None if n == "乙" else 0,
                                          source=m.SOURCE_WEB) for mo in (DEC, JAN) for n in ("甲", "乙")],
                          mode=BF)
    await _cover_all(session, m.MARKET_TWSE)
    await _cover_all(session, m.MARKET_TPEX)
    await import_transfers(session, m.TransferBatch(D10, [_decl("AAA", D10, 1000)]), m.MARKET_TWSE,
                           m.SOURCE_WEB, mode=FWD)
    await import_transfers(session, m.TransferBatch(D12, [_amend("AAA", D12, 600, D10)]), m.MARKET_TWSE,
                           m.SOURCE_WEB, mode=FWD)
    bbb = {**_decl("BBB", D12, 500), "market": m.MARKET_TPEX, "planned_trust_shares": None,
           "transfer_shares": None}
    await import_transfers(session, m.TransferBatch(D12, [bbb]), m.MARKET_TPEX, m.SOURCE_WEB, mode=FWD)


async def test_end_to_end_shadow_only_invariants(db_session):
    await _seed_history(db_session)
    (await db_session.get(Stock, "BBB")).market = m.MARKET_TPEX
    await db_session.commit()
    await build_features(db_session, TARGET)
    await persist_signals(db_session, TARGET)
    db_session.expunge_all()
    snap_before = _snapshot_state((await db_session.execute(select(SignalSnapshot))).scalars().all())
    feat_before = _feature_state((await db_session.execute(select(FeatureDaily))).scalars().all())
    raw_cfg = load_yaml_thresholds()
    version_before = data_version(raw_cfg)

    await _import_all(db_session)
    built = await build_mops_features(db_session, TARGET)
    assert built.rows == 3 and built.amendment_ambiguous == ()
    aaa, bbb, ccc = [await _feature(db_session, s) for s in ("AAA", "BBB", "CCC")]
    assert aaa.transfer_market_sale_ratio == pytest.approx(0.6)  # amendment 取代舊申報，非 1.6
    assert (aaa.holding_provenance, aaa.transfer_provenance) == (PROV_SAFE, PROV_SAFE)
    assert bbb.insider_pledge_ratio == 0.0 and bbb.transfer_provenance == PROV_SAFE  # TPEx 涵蓋可配對
    assert bbb.transfer_market_sale_ratio is None  # 信託分項未知 → NULL，不當 0
    assert ccc.insider_pledge_ratio is None  # 部分設質未知 → NULL
    assert ccc.holding_provenance == PROV_BACKFILL

    rows = (await db_session.execute(select(MopsShadowFeatureDaily))).scalars().all()
    df = pd.DataFrame([{"symbol": r.symbol, "data_date": r.data_date, "holding_provenance": r.holding_provenance,
                        "transfer_provenance": r.transfer_provenance,
                        "insider_holding_change_pct": r.insider_holding_change_pct} for r in rows])
    assert "CCC" not in df.loc[honest_values(df, "insider_holding_change_pct").notna(), "symbol"].tolist()

    # 冪等：重跑 importer 與 feature builder，raw 筆數與 shadow 特徵不變
    counts = await _raw_counts(db_session)
    shadow = _feature_state(rows)
    await _import_all(db_session)
    await build_mops_features(db_session, TARGET)
    db_session.expunge_all()
    assert await _raw_counts(db_session) == counts
    assert _feature_state((await db_session.execute(select(MopsShadowFeatureDaily))).scalars().all()) == shadow

    # shadow-only：feature_daily / signal_snapshot 完全不變
    await build_features(db_session, TARGET)
    await persist_signals(db_session, TARGET)
    db_session.expunge_all()
    assert _snapshot_state((await db_session.execute(select(SignalSnapshot))).scalars().all()) == snap_before
    assert _feature_state((await db_session.execute(select(FeatureDaily))).scalars().all()) == feat_before

    # data_version 不因 MOPS 設定改變；Phase 1 weights 無 MOPS 鍵
    changed = {**raw_cfg, "mops": {**raw_cfg["mops"], "research": {"min_honest_test_days": 99}}}
    assert data_version(changed) == version_before
    flat = str(get_thresholds().weights)
    assert not any(k in flat for k in ("insider", "pledge", "transfer", "major_holder", "mops"))
