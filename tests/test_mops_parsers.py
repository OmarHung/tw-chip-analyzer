"""Phase 2 MOPS parser / 揭露時點 / connector 錯誤分類（docs/14）。全檔禁止真實網路。"""
from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.connectors import mops as conn
from app.importers import mops as m

FX = Path(__file__).parent / "fixtures"
TPE = m.TPE


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """parser 測試不得連網：任何真實 httpx 傳輸都直接失敗。"""
    def _boom(*_a, **_k):
        raise AssertionError("測試不得發出真實網路請求")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _boom)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _boom)


def _json(name: str):
    return json.loads((FX / name).read_text(encoding="utf-8"))


def _html(name: str) -> str:
    return (FX / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 單位 / 格式

class TestConversions:
    def test_shares_thousands_and_blank(self):
        assert m.parse_shares("  1,653,709,980 ") == 1_653_709_980
        assert m.parse_shares("0") == 0  # 0 是有效值
        assert m.parse_shares("") is None  # 空白是未知，不當 0
        assert m.parse_shares("--") is None
        assert m.parse_shares("12.5") is None  # 股數不應有小數 → 異常值視為未知
        assert m.parse_shares("abc") is None

    def test_pct_keeps_exact_decimal(self):
        assert m.parse_pct("21.46%") == Decimal("21.46")
        assert m.parse_pct("100.00%") == Decimal("100.00")
        assert m.parse_pct("") is None
        assert m.parse_pct("N/A") is None
        assert m.parse_pct("x%") is None

    def test_roc_dates(self):
        assert m.parse_roc_any("1150820") == dt.date(2026, 8, 20)
        assert m.parse_roc_any("115/08/14") == dt.date(2026, 8, 14)
        assert m.parse_roc_any(" 100/3/10 ") == dt.date(2011, 3, 10)
        assert m.parse_roc_any("2026-08-14") is None
        assert m.roc_month_end("11507") == dt.date(2026, 7, 31)
        assert m.roc_month_end("11502") == dt.date(2026, 2, 28)
        assert m.roc_month_end("11513") is None

    def test_periods_both_formats(self):
        assert m.parse_period("1150914~1151013") == (dt.date(2026, 9, 14), dt.date(2026, 10, 13))
        assert m.parse_period("115/03/10 ~ 115/03/12") == (dt.date(2026, 3, 10), dt.date(2026, 3, 12))
        assert m.parse_period("") == (None, None)


class TestMethodCategory:
    @pytest.mark.parametrize("text,cat", [
        ("一般交易(每日得轉讓股數限制)", "market"),
        (" 盤後定價交易", "market"),
        ("一般交易(每日得轉讓股數限制) 鉅額逐筆交易", "market"),
        ("贈與", "gift"),
        ("信託", "trust"),
        ("洽特定人", "private"),
    ])
    def test_known_methods(self, text, cat):
        assert m.method_category(text) == cat

    @pytest.mark.parametrize("text", ["", None, "其他", "贈與 一般交易(每日得轉讓股數限制)", "轉讓私募股票"])
    def test_unknown_is_not_forced(self, text):
        # 無法判定或混合非市場方式 → unknown，不歸為市場賣壓（利空）也不歸利多
        assert m.method_category(text) == "unknown"


# ---------------------------------------------------------------- 揭露時點

class TestAvailability:
    def test_holding_rule_and_publish_date(self):
        # 11507 資料、出表日期 1150820 → max(次月 21 日 08:00, 出表日 +1 天 08:00)
        got = m.holding_available_at(dt.date(2026, 7, 31), dt.date(2026, 8, 20))
        assert got == dt.datetime(2026, 8, 21, 8, 0, tzinfo=TPE)
        # 出表日很晚（遲延/修正版）→ 取出表日，不早於官方可見
        late = m.holding_available_at(dt.date(2026, 7, 31), dt.date(2026, 9, 3))
        assert late == dt.datetime(2026, 9, 4, 8, 0, tzinfo=TPE)
        # 網頁回補無出表日期 → 只用規則日
        assert m.holding_available_at(dt.date(2026, 7, 31), None) == dt.datetime(2026, 8, 21, 8, 0, tzinfo=TPE)

    def test_holding_rule_clamps_short_month(self):
        from app.core.config import Thresholds
        t = Thresholds({"mops": {"insider_holding": {"available_day_of_next_month": 31}}})
        assert m.holding_available_at(dt.date(2026, 1, 31), None, t).date() == dt.date(2026, 2, 28)

    def test_transfer_rule_is_next_morning_and_tz_aware(self):
        got = m.transfer_available_at(dt.date(2026, 8, 14))
        assert got == dt.datetime(2026, 8, 15, 8, 0, tzinfo=TPE)
        assert got.tzinfo is not None

    def test_rules_read_config(self):
        from app.core.config import Thresholds
        t = Thresholds({"mops": {"transfer_declaration": {"available_lag_days": 2, "available_hour": 18}}})
        assert m.transfer_available_at(dt.date(2026, 8, 14), t) == dt.datetime(2026, 8, 16, 18, 0, tzinfo=TPE)


# ---------------------------------------------------------------- 持股

class TestHoldings:
    def test_twse_openapi_fixture(self):
        rows = m.parse_openapi_holdings(_json("mops_t187ap11_L.json"), m.MARKET_TWSE)
        tsmc = [r for r in rows if r["symbol"] == "2330" and r["holder_name"] == "魏哲家"]
        assert tsmc, "fixture 應含 2330 董事長"
        r = tsmc[0]
        assert r["data_date"] == dt.date(2026, 7, 31)
        assert r["report_date"] == dt.date(2026, 8, 20)
        assert r["source"] == m.SOURCE_OPENAPI and r["market"] == "TWSE"
        assert r["current_shares"] == 7_452_349 and r["pledged_shares"] == 1_600_000
        assert r["pledge_pct"] == Decimal("21.46")
        # TWSE 欄名 "選任時持股 " 帶尾隨空白仍要解析到
        assert r["shares_at_election"] == 6_392_834
        assert r["available_at"] == dt.datetime(2026, 8, 21, 8, 0, tzinfo=TPE)

    def test_tpex_openapi_fixture_key_without_trailing_space(self):
        rows = m.parse_openapi_holdings(_json("mops_t187ap11_O.json"), m.MARKET_TPEX)
        assert rows and all(r["market"] == "TPEx" for r in rows)  # 同 stock.market 拼法
        assert all(r["shares_at_election"] is not None for r in rows)

    def test_web_page_matches_openapi_same_month(self):
        web = m.parse_holdings_page(_html("mops_stapap1_sii_2330_11507.html"), "2330", m.MARKET_TWSE)
        api = [r for r in m.parse_openapi_holdings(_json("mops_t187ap11_L.json"), m.MARKET_TWSE)
               if r["symbol"] == "2330"]
        key = lambda r: (r["title"], r["holder_name"], r["current_shares"], r["pledged_shares"])  # noqa: E731
        assert sorted(map(key, web)) == sorted(map(key, api))
        assert all(r["source"] == m.SOURCE_WEB for r in web)
        # 網頁無出表日期：report_date/available_at 為規則推定，不是抓取時間
        assert {r["available_at"] for r in web} == {dt.datetime(2026, 8, 21, 8, 0, tzinfo=TPE)}

    def test_web_otc_page(self):
        rows = m.parse_holdings_page(_html("mops_stapap1_otc_5386_11507.html"), "5386", m.MARKET_TPEX)
        assert rows and rows[0]["holder_name"] == "柯聰源"
        assert rows[0]["current_shares"] == 11_049_540

    def test_blank_and_bad_values_stay_null(self):
        rec = {"出表日期": "1150820", "資料年月": "11507", "公司代號": "9999", "職稱": "董事本人",
               "姓名": "某甲", "選任時持股 ": "", "目前持股": "--", "設質股數": "1.5",
               "設質股數佔持股比例": "", "內部人關係人目前持股合計": "0",
               "內部人關係人設質股數": "0", "內部人關係人設質比例": "0.00%"}
        (r,) = m.parse_openapi_holdings([rec], m.MARKET_TWSE)
        assert r["shares_at_election"] is None and r["current_shares"] is None
        assert r["pledged_shares"] is None and r["pledge_pct"] is None
        assert r["related_shares"] == 0

    def test_invalid_symbols_and_dates_dropped(self):
        base = {"出表日期": "1150820", "資料年月": "11507", "職稱": "董事本人", "姓名": "某甲", "目前持股": "1"}
        recs = [{**base, "公司代號": "0050"}, {**base, "公司代號": "12345"},
                {**base, "公司代號": "2330", "資料年月": "abc"}]
        assert m.parse_openapi_holdings(recs, m.MARKET_TWSE) == []

    def test_web_page_company_must_match_query(self):
        """網頁列的 symbol 若只取自查詢參數，回應成別家公司會被寫成錯的代號與涵蓋紀錄。"""
        html = _html("mops_stapap1_sii_2330_11507.html")
        with pytest.raises(m.MopsPageError, match="公司代號"):
            m.parse_holdings_page(html, "2317", m.MARKET_TWSE)
        # 兩個代號標記不一致 → 無法確認是哪家公司
        with pytest.raises(m.MopsPageError, match="公司代號"):
            m.parse_holdings_page(html.replace("_co_id_hhc=2330__", "_co_id_hhc=2317__"), "2330",
                                  m.MARKET_TWSE)
        # 有資料列卻找不到任何代號標記 → 不可假設是查詢的公司
        stripped = html.replace("_co_id_hhc=2330__", "").replace(
            "<td class='compName'>2330", "<td class='compName'>")
        with pytest.raises(m.MopsPageError, match="公司代號"):
            m.parse_holdings_page(stripped, "2330", m.MARKET_TWSE)

    def test_web_page_market_must_match_query(self):
        with pytest.raises(m.MopsPageError, match="市場"):
            m.parse_holdings_page(_html("mops_stapap1_otc_5386_11507.html"), "5386", m.MARKET_TWSE)
        with pytest.raises(m.MopsPageError, match="市場"):
            m.parse_holdings_page(_html("mops_stapap1_sii_2330_11507.html"), "2330", m.MARKET_TPEX)

    def test_page_without_month_but_rows_is_error(self):
        with pytest.raises(m.MopsPageError):
            m.parse_holdings_page("<table><tr class='odd'><td>a</td></tr></table>", "2330", "TWSE")

    def test_only_official_no_data_marker_is_zero_rows(self):
        """實測（2026-09-14）官方零筆頁為 `資料庫中查無資料 !`（6919 民國 100/01）。
        原本以合成的 `<html>查無資料</html>` 當空頁是猜測，並非實測字樣，故不再視為零筆。"""
        assert m.parse_holdings_page(_html("mops_stapap1_sii_6919_10001_nodata.html"), "6919",
                                     m.MARKET_TWSE) == []

    @pytest.mark.parametrize("html", [
        "<html>系統忙碌，請稍後再試</html>",
        "<html></html>",
        "",
        "<html>查無資料</html>",  # 非官方實測字樣
    ])
    def test_page_without_rows_or_official_marker_is_error(self, html):
        with pytest.raises(m.MopsPageError):
            m.parse_holdings_page(html, "2330", m.MARKET_TWSE)

    def test_no_company_page_is_error_not_zero(self):
        """實測 `查無此公司資料`（9999）：公司不存在不是「該月零筆」，不可寫涵蓋。"""
        with pytest.raises(m.MopsPageError, match="查無此公司"):
            m.parse_holdings_page(_html("mops_stapap1_sii_9999_11507_nocompany.html"), "9999",
                                  m.MARKET_TWSE)

    def test_header_with_month_but_no_rows_is_error(self):
        html = _html("mops_stapap1_sii_2330_11507.html")
        no_rows = re.sub(r"<tr class='(?:odd|even)'>.*?</tr>", "", html, flags=re.S | re.I)
        assert "資料年月" in no_rows and not re.search(r"<tr class='(?:odd|even)'>", no_rows, re.I)
        with pytest.raises(m.MopsPageError):
            m.parse_holdings_page(no_rows, "2330", m.MARKET_TWSE)


# ---------------------------------------------------------------- 轉讓申報

class TestTransfers:
    def test_twse_page_and_amendment_chain(self):
        old = m.parse_transfer_page(_html("mops_t56sb12_SY_1150814.html"), "TWSE", dt.date(2026, 8, 14))
        new = m.parse_transfer_page(_html("mops_t56sb12_SY_1150818.html"), "TWSE", dt.date(2026, 8, 18))
        o = [r for r in old.rows if r["symbol"] == "2442" and r["superseded_on"]]
        n = [r for r in new.rows if r["symbol"] == "2442" and r["amends_report_date"]]
        assert len(o) == 1 and o[0]["superseded_on"] == dt.date(2026, 8, 18)
        assert o[0]["duplicate_count"] == 3  # 來源重複顯示 3 列 → 合併、記次數
        assert o[0]["amendment_note"].startswith("本單已於")
        assert n and n[0]["amends_report_date"] == dt.date(2026, 8, 14)
        assert o[0]["available_at"] == dt.datetime(2026, 8, 15, 8, 0, tzinfo=TPE)

    def test_tpex_page_has_17_columns(self):
        b = m.parse_transfer_page(_html("mops_t56sb12_OY_1150310.html"), m.MARKET_TPEX, dt.date(2026, 3, 10))
        got = {r["symbol"]: r for r in b.rows}
        assert set(got) == {"4120", "5386"}
        assert got["4120"]["method_category"] == "gift" and got["4120"]["transfer_shares"] is None
        assert got["5386"]["method_category"] == "market"
        assert got["5386"]["planned_own_shares"] == 1_000_000
        assert got["5386"]["unfinished_flag"] is None  # 上櫃頁無此欄

    def test_explicit_empty_day_is_coverage(self):
        b = m.parse_transfer_page(_html("mops_t56sb12_SY_1050310_empty.html"), "TWSE", dt.date(2016, 3, 10))
        assert b.rows == [] and b.report_date == dt.date(2016, 3, 10)

    def test_unexpected_page_is_error_not_zero(self):
        with pytest.raises(m.MopsPageError):
            m.parse_transfer_page("<html>系統忙碌</html>", "TWSE", dt.date(2026, 3, 10))

    def test_openapi_twse_and_tpex_keys(self):
        tw = m.parse_openapi_transfers(_json("mops_t187ap12_L.json"), m.MARKET_TWSE)
        assert tw.report_date == dt.date(2026, 9, 11)
        umc = next(r for r in tw.rows if r["symbol"] == "2303")
        assert umc["method_category"] == "market" and umc["transfer_shares"] == 1_600_000
        assert umc["effective_start"] == dt.date(2026, 9, 14)
        ox = m.parse_openapi_transfers(_json("mops_t187ap12_O.json"), m.MARKET_TPEX)
        assert ox.rows[0]["reporter_role"] == "董事本人"  # TPEx 欄名「申請人身分」
        assert ox.rows[0]["symbol"] == "6026"

    def test_openapi_empty_day_row_gives_coverage_without_rows(self):
        rec = {"出表日期": "1150912", "公司代號": "", "公司名稱": "", "申報人身分": "", "姓名": ""}
        b = m.parse_openapi_transfers([rec], m.MARKET_TWSE)
        assert b.rows == [] and b.report_date == dt.date(2026, 9, 12)

    def test_row_hash_same_across_openapi_and_web(self):
        web = m.parse_transfer_page(_html("mops_t56sb12_SY_1150911.html"), "TWSE", dt.date(2026, 9, 11))
        api = m.parse_openapi_transfers(_json("mops_t187ap12_L.json"), m.MARKET_TWSE)
        assert {r["row_hash"] for r in web.rows} == {r["row_hash"] for r in api.rows}

    def test_hash_ignores_retroactive_annotations(self):
        a = {k: None for k in m._HASH_FIELDS} | {"symbol": "2442", "data_date": dt.date(2026, 8, 14)}
        b = a | {"amendment_note": "本單已於115/08/18 申報變更", "unfinished_flag": "是"}
        assert m.row_hash(a) == m.row_hash(b)


# ---------------------------------------------------------------- connector 錯誤分類（mock 傳輸）

class TestConnectorRetry:
    async def test_transient_retried_then_success(self):
        calls = {"n": 0}
        sleeps: list[float] = []

        async def call():
            calls["n"] += 1
            if calls["n"] < 3:
                raise conn.MopsTransientError("reset")
            return "ok"

        async def fake_sleep(s):
            sleeps.append(s)

        assert await conn.with_retry(call, "t", retries=3, backoff_sec=1.0, sleep=fake_sleep) == "ok"
        assert calls["n"] == 3 and sleeps == [1.0, 2.0]

    async def test_transient_exhausted_raises(self):
        async def call():
            raise conn.MopsTransientError("timeout")

        async def fake_sleep(_s):
            return None

        with pytest.raises(conn.MopsTransientError):
            await conn.with_retry(call, "t", retries=2, backoff_sec=0, sleep=fake_sleep)

    async def test_blocked_not_retried(self):
        calls = {"n": 0}

        async def call():
            calls["n"] += 1
            raise conn.MopsBlockedError("security")

        with pytest.raises(conn.MopsBlockedError):
            await conn.with_retry(call, "t", retries=5, backoff_sec=0)
        assert calls["n"] == 1

    async def test_request_classifies_status_and_block_page(self, monkeypatch):
        responses = {
            "https://x/500": httpx.Response(502, text="bad"),
            "https://x/403": httpx.Response(403, text="no"),
            "https://x/404": httpx.Response(404, text="none"),
            "https://x/block": httpx.Response(200, text="因為安全性考量，您所執行的頁面無法呈現"),
            "https://x/ok": httpx.Response(200, text="資料年月:11507"),
        }
        transport = httpx.MockTransport(lambda req: responses[str(req.url)])
        real_client = httpx.AsyncClient

        def client(**kw):
            kw.pop("verify", None)
            return real_client(transport=transport, **kw)

        monkeypatch.setattr(conn.httpx, "AsyncClient", client)
        with pytest.raises(conn.MopsTransientError):
            await conn._request("GET", "https://x/500")
        with pytest.raises(conn.MopsBlockedError):
            await conn._request("GET", "https://x/403")
        with pytest.raises(conn.MopsHttpError):
            await conn._request("GET", "https://x/404")
        with pytest.raises(conn.MopsBlockedError):
            await conn._request("GET", "https://x/block")
        assert "11507" in await conn._request("GET", "https://x/ok")
