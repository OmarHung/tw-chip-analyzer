"""Phase 2 MOPS：匯入冪等、修正版、point-in-time 可見性、NULL 語意、shadow 不影響正式分數（docs/14）。"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlalchemy import func, inspect, select

from app.core.config import Thresholds, get_thresholds, load_yaml_thresholds
from app.core.threshold_registry import data_version
from app.db.models.features import FeatureDaily, SignalSnapshot
from app.db.models.market import DailyPrice
from app.db.models.mops import (
    InsiderHoldingMonthly,
    InsiderTransferDeclaration,
    MopsFetchCoverage,
    MopsShadowFeatureDaily,
)
from app.importers import mops as m
from app.importers.base import availability_for
from app.importers.mops_service import import_holdings, import_transfers
from app.services.feature_builder import build_features
from app.services.mops_features import (
    build_mops_features,
    feature_as_of,
    latest_snapshots,
    transfer_sale_shares,
)
from app.services.signal_persist import persist_signals
from tests.test_signal_persist import TARGET, _seed_history

FX = Path(__file__).parent / "fixtures"
TPE = m.TPE
DEC, JAN, FEB = dt.date(2025, 12, 31), dt.date(2026, 1, 31), dt.date(2026, 2, 28)


def _hold(sym: str, month: dt.date, name: str, cur: int | None, pledged: int | None = 0, *,
          title: str = "董事本人", source: str = m.SOURCE_OPENAPI,
          report: dt.date | None = None) -> dict:
    report = report or (month + dt.timedelta(days=20))
    av = m.holding_available_at(month, report if source == m.SOURCE_OPENAPI else None)
    return {
        "symbol": sym, "market": "TWSE", "source": source, "data_date": month,
        "report_date": report if source == m.SOURCE_OPENAPI else av.date(), "available_at": av,
        "title": title, "holder_name": name, "row_seq": 0, "shares_at_election": None,
        "current_shares": cur, "pledged_shares": pledged, "pledge_pct": None,
        "related_shares": None, "related_pledged_shares": None, "related_pledge_pct": None,
    }


def _decl(sym: str, day: dt.date, planned: int | None, method: str = "一般交易(每日得轉讓股數限制)",
          superseded_on: dt.date | None = None, name: str = "某甲") -> dict:
    row = {
        "symbol": sym, "market": "TWSE", "source": m.SOURCE_WEB, "data_date": day,
        "available_at": m.transfer_available_at(day), "reporter_role": "董事本人",
        "reporter_name": name, "transfer_method": method, "method_category": m.method_category(method),
        "transfer_shares": planned, "max_intraday_shares": None, "transferee": None,
        "current_own_shares": None, "current_trust_shares": None,
        "planned_own_shares": planned, "planned_trust_shares": 0 if planned is not None else None,
        "after_own_shares": None, "after_trust_shares": None,
        "effective_start": None, "effective_end": None, "amendment_note": None,
        "amends_report_date": None, "superseded_on": superseded_on, "unfinished_flag": None,
        "duplicate_count": 1,
    }
    row["row_hash"] = m.row_hash(row)
    return row


async def _ih(session, rows: list[dict], mode: str = m.MODE_FORWARD) -> int:
    return await import_holdings(session, rows, mode=mode)


async def _it(session, batch, market: str, source: str, mode: str = m.MODE_FORWARD) -> int:
    return await import_transfers(session, batch, market, source, mode=mode)


async def _cover_all(session, market: str = "TWSE") -> None:
    days = (await session.execute(select(DailyPrice.data_date).distinct())).scalars().all()
    for d in days:
        await _it(session, m.TransferBatch(d, []), market, m.SOURCE_WEB)


async def _feature(session, sym: str, day: dt.date = TARGET) -> MopsShadowFeatureDaily:
    session.expunge_all()
    return (await session.execute(select(MopsShadowFeatureDaily).where(
        MopsShadowFeatureDaily.symbol == sym, MopsShadowFeatureDaily.data_date == day
    ))).scalar_one()


async def _add_price_day(session, day: dt.date) -> None:
    for sym, px in (("AAA", 113.0), ("BBB", 63.0), ("CCC", 213.0)):
        session.add(DailyPrice(symbol=sym, data_date=day, available_at=availability_for(day),
                               open=px, high=px, low=px, close=px, volume=1_000_000, turnover=px * 1e6))
    await session.commit()


# ---------------------------------------------------------------- 匯入冪等 / 修正版

async def test_import_holdings_idempotent(db_session):
    await _seed_history(db_session)
    rows = [_hold("AAA", JAN, "甲", 1000), _hold("AAA", JAN, "甲", 1000, title="大股東本人")]
    await _ih(db_session, rows)
    await _ih(db_session, rows)
    assert (await db_session.execute(select(func.count()).select_from(InsiderHoldingMonthly))).scalar() == 2


async def test_same_title_and_name_rows_kept_via_row_seq(db_session):
    """實測全市場：法人董事一人多席會以相同 (職稱, 姓名) 出現多列；不可因唯一鍵衝突失敗或丟列。"""
    await _seed_history(db_session)
    seat1 = {**_hold("AAA", JAN, "嘉新水泥股份有限公司", 274_237_776), "shares_at_election": 239_629_776}
    seat2 = {**_hold("AAA", JAN, "嘉新水泥股份有限公司", 274_237_776), "shares_at_election": 0}
    rows = m.assign_row_seq([{k: v for k, v in r.items() if k != "row_seq"} for r in (seat1, seat2)])
    assert [r["row_seq"] for r in rows] == [0, 1]
    await _ih(db_session, rows)
    await _ih(db_session, rows)
    assert (await db_session.execute(select(func.count()).select_from(InsiderHoldingMonthly))).scalar() == 2


async def test_holding_revision_kept_as_new_version_and_used_only_after_available(db_session):
    await _seed_history(db_session)
    original = _hold("AAA", JAN, "甲", 1000, report=dt.date(2026, 2, 20))
    revised = _hold("AAA", JAN, "甲", 2000, report=dt.date(2026, 3, 25))
    await _ih(db_session, [original])
    await _ih(db_session, [revised])
    assert (await db_session.execute(select(func.count()).select_from(InsiderHoldingMonthly))).scalar() == 2

    rows = [original, revised]
    before = latest_snapshots(rows, dt.datetime(2026, 3, 20, 15, tzinfo=TPE))
    after = latest_snapshots(rows, dt.datetime(2026, 3, 26, 15, tzinfo=TPE))
    assert before["AAA"][JAN][0]["current_shares"] == 1000  # 修正版尚未揭露
    assert after["AAA"][JAN][0]["current_shares"] == 2000


async def test_transfer_import_idempotent_and_openapi_does_not_wipe_annotations(db_session):
    web = m.parse_transfer_page((FX / "mops_t56sb12_SY_1150814.html").read_text("utf-8"),
                                "TWSE", dt.date(2026, 8, 14))
    await _it(db_session, web, "TWSE", m.SOURCE_WEB)
    await _it(db_session, web, "TWSE", m.SOURCE_WEB)
    n = (await db_session.execute(select(func.count()).select_from(InsiderTransferDeclaration))).scalar()
    assert n == len(web.rows)

    # 同一筆以 OpenAPI 形式（無異動註記）再匯入：不新增列、不洗掉網頁註記
    api_like = m.TransferBatch(web.report_date, [
        {**r, "source": m.SOURCE_OPENAPI, "amendment_note": None, "superseded_on": None}
        for r in web.rows
    ])
    await _it(db_session, api_like, "TWSE", m.SOURCE_OPENAPI)
    assert (await db_session.execute(select(func.count()).select_from(InsiderTransferDeclaration))).scalar() == n
    sup = (await db_session.execute(select(InsiderTransferDeclaration.superseded_on).where(
        InsiderTransferDeclaration.symbol == "2442", InsiderTransferDeclaration.superseded_on.is_not(None)
    ))).scalars().all()
    assert sup == [dt.date(2026, 8, 18)]

    cov = (await db_session.execute(select(MopsFetchCoverage))).scalars().all()
    assert len(cov) == 1 and cov[0].row_count == len(web.rows)


async def test_web_reimport_updates_retroactive_annotation(db_session):
    day = dt.date(2026, 3, 10)
    first = m.TransferBatch(day, [_decl("AAA", day, 500)])
    await _it(db_session, first, "TWSE", m.SOURCE_WEB)
    later = m.TransferBatch(day, [{**first.rows[0], "superseded_on": dt.date(2026, 3, 12),
                                   "amendment_note": "本單已於115/03/12 申報變更"}])
    await _it(db_session, later, "TWSE", m.SOURCE_WEB)
    db_session.expunge_all()
    rows = (await db_session.execute(select(InsiderTransferDeclaration))).scalars().all()
    assert len(rows) == 1 and rows[0].superseded_on == dt.date(2026, 3, 12)


async def test_empty_day_recorded_as_zero_coverage(db_session):
    await _it(db_session, m.TransferBatch(dt.date(2016, 3, 10), []), "TWSE", m.SOURCE_WEB)
    (cov,) = (await db_session.execute(select(MopsFetchCoverage))).scalars().all()
    assert cov.row_count == 0 and cov.dataset == m.DATASET_TRANSFER


# ---------------------------------------------------------------- point-in-time

async def test_holding_not_visible_before_available_then_visible(db_session):
    await _seed_history(db_session)
    rows = [_hold(s, mo, "甲", 1000 + i) for i, mo in enumerate((DEC, JAN, FEB)) for s in ("AAA", "BBB")]
    await _ih(db_session, rows)
    # FEB 資料 available_at = 2026-03-21 08:00 → 03-20 盤後不可見
    assert m.holding_available_at(FEB, FEB + dt.timedelta(days=20)) > feature_as_of(TARGET)
    await build_mops_features(db_session, TARGET)
    assert (await _feature(db_session, "AAA")).holding_month == JAN

    later = dt.date(2026, 3, 23)
    await _add_price_day(db_session, later)
    await build_mops_features(db_session, later)
    got = await _feature(db_session, "AAA", later)
    assert got.holding_month == FEB
    assert got.insider_holding_change_pct == (1002 - 1001) / 1001


def test_superseded_declaration_drops_only_after_amendment_available():
    as_of = dt.datetime(2026, 3, 20, 15, tzinfo=TPE)
    early = _decl("AAA", dt.date(2026, 3, 10), 1000, superseded_on=dt.date(2026, 3, 19))
    known_later = _decl("BBB", dt.date(2026, 3, 10), 1000, superseded_on=dt.date(2026, 3, 20))
    not_yet = _decl("CCC", dt.date(2026, 3, 20), 700)  # 03-20 申報 → 03-21 08:00 才可用
    got = transfer_sale_shares([early, known_later, not_yet], as_of)
    assert "AAA" not in got  # 變更申報 03-19 已可用 → 舊申報剔除
    assert got["BBB"] == 1000  # 變更申報 03-21 才可用 → 03-20 仍計入舊申報
    assert "CCC" not in got


def test_non_market_methods_not_counted():
    as_of = dt.datetime(2026, 3, 20, 15, tzinfo=TPE)
    decls = [_decl("AAA", dt.date(2026, 3, 10), 900, method="贈與"),
             _decl("AAA", dt.date(2026, 3, 11), 900, method="其他方式", name="乙"),
             _decl("AAA", dt.date(2026, 3, 12), None, name="丙")]
    got = transfer_sale_shares(decls, as_of)
    assert got["AAA"] is None  # market 類總股數未知 → 未知，不當 0
    assert transfer_sale_shares(decls[:2], as_of) == {}  # gift/unknown 不計入


# ---------------------------------------------------------------- NULL 語意 / 橫斷面 z

async def test_missing_data_null_and_true_zero_distinguished(db_session):
    await _seed_history(db_session)
    await _ih(db_session, [_hold(s, mo, "甲", 1000) for mo in (DEC, JAN) for s in ("AAA", "BBB")])
    # 無涵蓋 → 轉讓特徵 NULL（不能說「沒有申報」）
    await build_mops_features(db_session, TARGET)
    aaa, ccc = await _feature(db_session, "AAA"), await _feature(db_session, "CCC")
    assert aaa.transfer_market_sale_ratio is None
    assert aaa.insider_pledge_ratio == 0.0  # 持股已知、設質 0 → 真的 0
    assert ccc.holding_month is None and ccc.insider_pledge_ratio is None  # 無持股資料 → NULL，不當 0
    assert ccc.transfer_market_sale_ratio is None

    await _cover_all(db_session)
    await build_mops_features(db_session, TARGET)
    aaa = await _feature(db_session, "AAA")
    assert aaa.transfer_market_sale_ratio == 0.0  # 窗內每日涵蓋、確實無申報 → 有效 0


async def test_tpex_stock_matches_tpex_transfer_coverage(db_session):
    """主檔 market="TPEx"（app/importers/tpex.py）必須對上 MOPS 涵蓋紀錄的市場名，
    否則上櫃股轉讓特徵永遠 NULL。"""
    from app.db.models.market import Stock

    await _seed_history(db_session)
    (await db_session.get(Stock, "BBB")).market = "TPEx"
    await db_session.commit()
    await _ih(db_session, [_hold("BBB", mo, "甲", 1000) for mo in (DEC, JAN)])
    await _cover_all(db_session, m.MARKET_TPEX)
    await build_mops_features(db_session, TARGET)
    assert (await _feature(db_session, "BBB")).transfer_market_sale_ratio == 0.0
    assert (await _feature(db_session, "AAA")).transfer_market_sale_ratio is None  # TWSE 未涵蓋


async def test_mops_stub_uses_stock_master_tpex_spelling(db_session):
    from app.db.models.market import Stock

    row = {**_hold("NEWO", JAN, "甲", 1000), "market": m.MARKET_TPEX}
    await _ih(db_session, [row])
    db_session.expunge_all()
    assert (await db_session.get(Stock, "NEWO")).market == "TPEx"


async def test_holdings_backfill_targets_include_tpex_stocks(db_session):
    from app.db.models.market import Stock
    from scripts.backfill_mops_holdings import _targets

    await _seed_history(db_session)
    (await db_session.get(Stock, "BBB")).market = "TPEx"
    await db_session.commit()
    assert dict(await _targets(10, ["AAA", "BBB"])) == {"AAA": "TWSE", "BBB": "TPEx"}
    assert dict(await _targets(10, None)) == {"AAA": "TWSE", "BBB": "TPEx", "CCC": "TWSE"}


async def test_window_insufficient_is_null(db_session):
    await _seed_history(db_session)
    await _ih(db_session, [_hold("AAA", mo, "甲", 1000) for mo in (DEC, JAN)])
    await _cover_all(db_session)
    raw = load_yaml_thresholds()
    raw["mops"]["transfer_declaration"]["window_bars"] = 40
    await build_mops_features(db_session, TARGET, Thresholds(raw))
    assert (await _feature(db_session, "AAA")).transfer_market_sale_ratio is None  # 只有 24 個可用交易日


async def test_missing_previous_month_change_is_null(db_session):
    await _seed_history(db_session)
    await _ih(db_session, [_hold("AAA", JAN, "甲", 1000), _hold("BBB", JAN, "甲", 1000)])
    await build_mops_features(db_session, TARGET)
    aaa = await _feature(db_session, "AAA")
    assert aaa.insider_holding_change_pct is None and aaa.insider_pledge_ratio_change is None
    assert aaa.major_holder_change_pct is None


async def test_stale_holding_month_is_null(db_session):
    await _seed_history(db_session)
    old = [_hold("AAA", mo, "甲", 1000) for mo in (dt.date(2025, 10, 31), dt.date(2025, 11, 30))]
    await _ih(db_session, old)
    await build_mops_features(db_session, TARGET)
    assert (await _feature(db_session, "AAA")).holding_month is None  # 超過 max_stale_months


async def test_single_stock_cross_section_z_is_null(db_session):
    await _seed_history(db_session)
    await _ih(db_session, [_hold("AAA", DEC, "甲", 1000), _hold("AAA", JAN, "甲", 1100)])
    await build_mops_features(db_session, TARGET)
    aaa = await _feature(db_session, "AAA")
    assert aaa.insider_holding_change_pct == 0.1
    assert aaa.insider_holding_change_z is None  # 單檔無法形成橫斷面 z


async def test_identical_values_give_valid_neutral_zero(db_session):
    await _seed_history(db_session)
    rows = [_hold(s, mo, "甲", v) for s in ("AAA", "BBB") for mo, v in ((DEC, 1000), (JAN, 1100))]
    await _ih(db_session, rows)
    await build_mops_features(db_session, TARGET)
    for sym in ("AAA", "BBB"):
        f = await _feature(db_session, sym)
        assert f.insider_holding_change_z == 0.0
        assert f.insider_pledge_ratio_z == 0.0


async def test_multi_title_same_person_not_double_counted_and_roster_change_ignored(db_session):
    await _seed_history(db_session)
    rows = [
        _hold("AAA", DEC, "甲", 1000), _hold("AAA", DEC, "甲", 1000, title="大股東本人"),
        _hold("AAA", JAN, "甲", 1200, pledged=600), _hold("AAA", JAN, "甲", 1200, pledged=600, title="大股東本人"),
        _hold("AAA", JAN, "新任董事", 50_000),  # 改選新進者不算持股變化
    ]
    await _ih(db_session, rows)
    await build_mops_features(db_session, TARGET)
    f = await _feature(db_session, "AAA")
    assert f.insider_holding_change_pct == 0.2
    assert f.major_holder_change_pct == 0.2
    assert f.insider_pledge_ratio == 600 / 51_200


# ---------------------------------------------------------------- shadow-only：正式分數不變

def _snapshot_state(rows) -> dict:
    return {
        r.symbol: (r.chip_score, r.intraday_score, r.institutional_score, r.holder_score,
                   r.market_score, r.action, r.reasons, r.risk_reward, r.stop_loss, r.tp1, r.tp2,
                   r.payload, r.config_version)
        for r in rows
    }


def _feature_state(rows) -> dict:
    skip = {"id", "created_at"}
    return {r.symbol: {a.key: getattr(r, a.key) for a in inspect(r).mapper.column_attrs if a.key not in skip}
            for r in rows}


async def test_mops_data_does_not_change_chip_score_breakdown_or_action(db_session):
    await _seed_history(db_session)
    await build_features(db_session, TARGET)
    await persist_signals(db_session, TARGET)
    db_session.expunge_all()
    snap_before = _snapshot_state((await db_session.execute(select(SignalSnapshot))).scalars().all())
    feat_before = _feature_state((await db_session.execute(select(FeatureDaily))).scalars().all())
    assert snap_before, "baseline 必須有訊號"

    # 寫入會讓 shadow 特徵差異很大的 MOPS 資料（高質押、大量市場轉讓申報）
    await _ih(db_session, [
        _hold(s, mo, "甲", 1000, pledged=900 if s == "CCC" else 0)
        for mo in (DEC, JAN) for s in ("AAA", "BBB", "CCC")
    ])
    await _cover_all(db_session)
    day = dt.date(2026, 3, 12)
    await _it(db_session, m.TransferBatch(day, [_decl("AAA", day, 800)]), "TWSE", m.SOURCE_WEB)
    assert (await build_mops_features(db_session, TARGET)).rows == 3
    assert (await _feature(db_session, "AAA")).transfer_market_sale_ratio == 0.8

    await build_features(db_session, TARGET)
    await persist_signals(db_session, TARGET)
    db_session.expunge_all()
    snap_after = _snapshot_state((await db_session.execute(select(SignalSnapshot))).scalars().all())
    feat_after = _feature_state((await db_session.execute(select(FeatureDaily))).scalars().all())
    assert snap_after == snap_before
    assert feat_after == feat_before


def test_phase2_has_no_weights_and_does_not_change_data_version():
    flat = str(get_thresholds().weights)
    for key in ("insider", "pledge", "transfer", "major_holder", "mops"):
        assert key not in flat
    raw = load_yaml_thresholds()
    without = {k: v for k, v in raw.items() if k != "mops"}
    assert data_version(raw) == data_version(without)  # 改 MOPS 設定不觸發正式分數重建
