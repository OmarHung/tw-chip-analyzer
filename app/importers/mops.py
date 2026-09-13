"""MOPS Phase 2 解析（純函式，不連網路）。語意與依據見 docs/14。

- 持股/質押：OpenAPI t187ap11_L / mopsfin_t187ap11_O（JSON）、MOPS ajax_stapap1（HTML）。
- 轉讓事前申報：OpenAPI t187ap12_L / mopsfin_t187ap12_O（JSON）、MOPS ajax_t56sb12（HTML）。

上市/上櫃欄名差異集中在 `_pick`：TWSE `"選任時持股 "` 帶尾隨空白、TPEx 轉讓表用
`Date` / `SecuritiesCompanyCode` / `申請人身分`。數字、百分比、民國日期轉換集中在本檔 helper。
`available_at` 一律由 `holding_available_at` / `transfer_available_at` 依 config 規則推導，
**不使用抓取時間**。
"""
from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.core.config import Thresholds, get_thresholds
from app.importers.base import is_stock_symbol, parse_roc_compact_date, parse_roc_date

TPE = ZoneInfo("Asia/Taipei")

SOURCE_OPENAPI = "openapi"
SOURCE_WEB = "mops_web"
DATASET_TRANSFER = "insider_transfer"
DATASET_HOLDING = "insider_holding"
SCOPE_ALL = "*"  # 涵蓋紀錄範圍：轉讓申報一次涵蓋全市場；持股網頁以個股代號為範圍

# 首次觀測模式（provenance，docs/14 §7.1）：只有 forward 可能進 honest OOS
MODE_FORWARD = "forward"
MODE_BACKFILL = "backfill"
MODE_UNKNOWN = "unknown"  # 遷移前舊列，無法判定

# 必須與股票主檔 `stock.market` 同拼法（app/importers/tpex.py 寫 "TPEx"）：
# 回補選股、_ensure_stocks stub、特徵的市場涵蓋比對都靠字串相等
MARKET_TWSE = "TWSE"
MARKET_TPEX = "TPEx"

_NULLS = {"", "--", "---", "-", "N/A", "n/a"}

# 轉讓方式 → 類別。只有明確全屬市場交易方式者歸 market；其餘不強制歸類（鐵則 7 精神）。
_MARKET_METHODS = ("一般交易", "盤後定價交易", "鉅額逐筆交易", "鉅額配對交易")
_SINGLE_CATEGORY = {"贈與": "gift", "信託": "trust", "洽特定人": "private"}


# ---------------------------------------------------------------- 單位/格式轉換

def clean_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def parse_shares(value) -> int | None:
    """股數：去千分位與空白；空白/非整數 → None（未知，不當 0）。"""
    s = clean_text(value).replace(",", "")
    if s in _NULLS or not re.fullmatch(r"-?\d+", s):
        return None
    return int(s)


def parse_pct(value) -> Decimal | None:
    """官方百分比 `"21.46%"` → Decimal('21.46')（保留原值，單位 %）。"""
    s = clean_text(value).replace(",", "").rstrip("%").strip()
    if s in _NULLS:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_roc_any(value) -> dt.date | None:
    """民國日期：`1150820`、`115/08/14`、`115/8/14`。"""
    s = clean_text(value)
    if re.fullmatch(r"\d{7}", s):
        return parse_roc_compact_date(s)
    if re.fullmatch(r"\d{2,3}/\d{1,2}/\d{1,2}", s):
        return parse_roc_date(s)
    return None


def roc_month_end(value) -> dt.date | None:
    """資料年月 `11507` → 2026-07-31。"""
    s = clean_text(value)
    if not re.fullmatch(r"\d{5}", s):
        return None
    y, m = int(s[:3]) + 1911, int(s[3:])
    if not 1 <= m <= 12:
        return None
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def parse_period(value) -> tuple[dt.date | None, dt.date | None]:
    """有效轉讓期間 `1150914~1151013` 或 `115/03/10 ~ 115/03/12`。"""
    parts = [p for p in re.split(r"~", clean_text(value)) if p.strip()]
    if len(parts) != 2:
        return None, None
    return parse_roc_any(parts[0]), parse_roc_any(parts[1])


def method_category(method: str | None) -> str:
    """轉讓方式分類：market / gift / trust / private / unknown（無法判定不歸利多利空）。"""
    text = clean_text(method)
    if not text:
        return "unknown"
    if text in _SINGLE_CATEGORY:
        return _SINGLE_CATEGORY[text]
    rest = text
    for m in _MARKET_METHODS:
        rest = rest.replace(m, "")
    rest = re.sub(r"[()（）\s]|每日得轉讓股數限制", "", rest)
    if rest == "" and any(m in text for m in _MARKET_METHODS):
        return "market"
    return "unknown"


# ---------------------------------------------------------------- 揭露時點規則

def _at(day: dt.date, hour: int) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, 0, tzinfo=TPE)


def holding_available_at(
    month_end: dt.date, report_date: dt.date | None, t: Thresholds | None = None
) -> dt.datetime:
    """持股月資料最早可用時點（docs/14 §2.3）。report_date=None（網頁回補）只用規則日。"""
    cfg = (t or get_thresholds()).get("mops", "insider_holding", default={}) or {}
    hour = int(cfg.get("available_hour", 8))
    nxt = month_end + dt.timedelta(days=1)
    day = min(int(cfg.get("available_day_of_next_month", 21)),
              calendar.monthrange(nxt.year, nxt.month)[1])
    rule = _at(dt.date(nxt.year, nxt.month, day), hour)
    if report_date is None:
        return rule
    published = _at(report_date + dt.timedelta(days=int(cfg.get("publish_lag_days", 1))), hour)
    return max(rule, published)


def transfer_available_at(report_date: dt.date, t: Thresholds | None = None) -> dt.datetime:
    """轉讓事前申報最早可用時點（docs/14 §3.5）。"""
    cfg = (t or get_thresholds()).get("mops", "transfer_declaration", default={}) or {}
    return _at(
        report_date + dt.timedelta(days=int(cfg.get("available_lag_days", 1))),
        int(cfg.get("available_hour", 8)),
    )


# ---------------------------------------------------------------- 持股/質押

def _pick(record: dict, *names: str):
    """依候選欄名取值；欄名比對前先 strip（TWSE `"選任時持股 "` 帶尾隨空白）。"""
    norm = {str(k).strip(): v for k, v in record.items()}
    for n in names:
        if n in norm:
            return norm[n]
    return None


def _holding_row(
    *, symbol: str, market: str, source: str, month_end: dt.date,
    report_date: dt.date, available_at: dt.datetime, values: dict,
) -> dict:
    return {
        "symbol": symbol, "market": market, "source": source,
        "data_date": month_end, "report_date": report_date, "available_at": available_at,
        **values,
    }


def _holding_values(title, name, elected, current, pledged, pct, rel, rel_pledged, rel_pct) -> dict:
    return {
        "title": clean_text(title),
        "holder_name": clean_text(name),
        "shares_at_election": parse_shares(elected),
        "current_shares": parse_shares(current),
        "pledged_shares": parse_shares(pledged),
        "pledge_pct": parse_pct(pct),
        "related_shares": parse_shares(rel),
        "related_pledged_shares": parse_shares(rel_pledged),
        "related_pledge_pct": parse_pct(rel_pct),
    }


def assign_row_seq(rows: list[dict]) -> list[dict]:
    """同 (symbol, 月, source, report_date, 職稱, 姓名) 依來源順序編 row_seq（0 起）。

    法人董事一人多席、同名不同人都會讓 (職稱, 姓名) 重複；以出現序區分才能忠實保存每一列，
    重匯同一份來源（順序不變）仍冪等。
    """
    seen: dict[tuple, int] = {}
    out: list[dict] = []
    for r in rows:
        key = (r["symbol"], r["data_date"], r["source"], r["report_date"], r["title"], r["holder_name"])
        out.append({**r, "row_seq": seen.get(key, 0)})
        seen[key] = seen.get(key, 0) + 1
    return out


def parse_openapi_holdings(
    records: list[dict], market: str, t: Thresholds | None = None
) -> list[dict]:
    """OpenAPI 董監事持股餘額明細 → insider_holding_monthly 列（上市/上櫃同構）。"""
    out: list[dict] = []
    for r in records:
        sym = clean_text(_pick(r, "公司代號", "SecuritiesCompanyCode"))
        month_end = roc_month_end(_pick(r, "資料年月"))
        report_date = parse_roc_any(_pick(r, "出表日期", "Date"))
        values = _holding_values(
            _pick(r, "職稱"), _pick(r, "姓名"), _pick(r, "選任時持股"), _pick(r, "目前持股"),
            _pick(r, "設質股數"), _pick(r, "設質股數佔持股比例"),
            _pick(r, "內部人關係人目前持股合計"), _pick(r, "內部人關係人設質股數"),
            _pick(r, "內部人關係人設質比例"),
        )
        if (not is_stock_symbol(sym) or month_end is None or report_date is None
                or not values["title"] or not values["holder_name"]):
            continue
        out.append(_holding_row(
            symbol=sym, market=market, source=SOURCE_OPENAPI, month_end=month_end,
            report_date=report_date,
            available_at=holding_available_at(month_end, report_date, t), values=values,
        ))
    return assign_row_seq(out)


def _cells(row_html: str) -> list[str]:
    """以 `<td` 開頭切格（MOPS 第一格結束標籤寫壞成 `</d>`，不能依賴結束標籤）。"""
    parts = re.split(r"<td[^>]*>", row_html, flags=re.I)[1:]
    return [
        clean_text(re.sub(r"<[^>]+>", " ", re.split(r"</t?d>", p, flags=re.I)[0]).replace("&nbsp;", " "))
        for p in parts
    ]


class MopsPageError(ValueError):
    """頁面結構不符預期或不是可證明的結果（忙碌頁、空白頁、身分不符…）。

    connector 不就地重試；呼叫端不寫涵蓋，留待下次回補重新請求。
    """


_PAGE_MARKET = {"上市公司": MARKET_TWSE, "上櫃公司": MARKET_TPEX}
# 實測（2026-09-14）官方回應字樣：公司存在但該月無資料 / 公司不存在（fixture: mops_stapap1_sii_*_nodata/nocompany）
_HOLDING_NO_DATA = "資料庫中查無資料"
_HOLDING_NO_COMPANY = "查無此公司資料"


def _check_holdings_page_identity(html: str, symbol: str, market: str, has_rows: bool) -> None:
    """回應頁的公司代號／市場必須與查詢一致（網頁列的 symbol 取自查詢參數，不核對就會張冠李戴）。

    實測標記：`<td class='compName'>2330台灣積體電路…`、`<!_co_id_hhc=2330__>`、`本資料由　(上市公司)`。
    - 任一代號標記與查詢不符，或兩標記互相矛盾 → MopsPageError。
    - 有資料列卻完全找不到代號標記 → MopsPageError（不可假設是查詢的公司）。
    - 官方零筆頁（`資料庫中查無資料`）沒有任何標記，無從核對身分；是否接受由 parse_holdings_page 決定。
    - 市場字樣存在且與查詢市場不符 → MopsPageError。
    """
    codes = set(re.findall(r"_co_id_hhc=(\w+?)__", html))
    codes |= set(re.findall(r"class='compName'>\s*(\d{4,6})", html))
    if (codes and codes != {symbol}) or (has_rows and not codes):
        raise MopsPageError(f"持股頁公司代號 {sorted(codes) or '缺漏'} ≠ 查詢 {symbol}")
    markets = {_PAGE_MARKET[k] for k in re.findall(r"\((上市公司|上櫃公司)\)", html)}
    if markets and markets != {market}:
        raise MopsPageError(f"持股頁市場 {sorted(markets)} ≠ 查詢 {symbol} {market}")


def parse_holdings_page(
    html: str, symbol: str, market: str, t: Thresholds | None = None
) -> list[dict]:
    """MOPS ajax_stapap1（單公司單月）→ insider_holding_monthly 列。

    網頁無出表日期：report_date 存推定揭露日（規則日），source=mops_web（docs/14 §2.4）。

    **只有命中官方零筆字樣 `資料庫中查無資料` 才回空 list**（呼叫端據此寫零筆涵蓋）。其餘一律 MopsPageError、
    不寫涵蓋、下次重試：`查無此公司資料`、系統忙碌/空白 HTML 等無資料列也無官方字樣的頁、
    有資料年月卻無有效資料列、有資料列卻找不到資料年月、公司代號/市場與查詢不符。
    MOPS 會忽略 TYPEK（實測以錯的市場查仍回該公司資料頁），故市場只能靠頁面字樣核對。
    """
    m = re.search(r"資料年月[:：]\s*(\d{5})", html)
    rows = re.findall(r"<tr class='(?:odd|even)'>(.*?)</tr>", html, flags=re.S | re.I)
    if not rows:
        if _HOLDING_NO_COMPANY in html:
            raise MopsPageError(f"{symbol} 查無此公司資料（不是該月零筆）")
        if _HOLDING_NO_DATA in html and m is None:
            return []
        raise MopsPageError(f"{symbol} 持股頁無資料列也無官方零筆字樣（忙碌頁/空白頁/結構變更）")
    _check_holdings_page_identity(html, symbol, market, has_rows=True)
    if m is None:
        raise MopsPageError(f"{symbol} 持股頁有資料列但找不到資料年月")
    month_end = roc_month_end(m.group(1))
    if month_end is None:
        raise MopsPageError(f"{symbol} 資料年月格式錯誤：{m.group(1)}")
    available_at = holding_available_at(month_end, None, t)
    out: list[dict] = []
    for row in rows:
        c = _cells(row)
        if len(c) < 9:
            continue
        values = _holding_values(*c[:9])
        if not values["title"] or not values["holder_name"]:
            continue
        out.append(_holding_row(
            symbol=symbol, market=market, source=SOURCE_WEB, month_end=month_end,
            report_date=available_at.date(), available_at=available_at, values=values,
        ))
    if not out:
        raise MopsPageError(f"{symbol} 持股頁有資料列但無一列可解析（欄數/職稱/姓名缺漏）")
    return assign_row_seq(out)


# ---------------------------------------------------------------- 轉讓事前申報

_HASH_FIELDS = (
    "symbol", "data_date", "reporter_role", "reporter_name", "transfer_method",
    "transfer_shares", "max_intraday_shares", "transferee",
    "current_own_shares", "current_trust_shares", "planned_own_shares",
    "planned_trust_shares", "after_own_shares", "after_trust_shares",
    "effective_start", "effective_end",
)


def row_hash(row: dict) -> str:
    """核心欄位 hash（不含事後回寫的異動情形/未完成註記），跨 OpenAPI/網頁一致。"""
    payload = "|".join("" if row.get(k) is None else str(row[k]) for k in _HASH_FIELDS)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Amendment:
    note: str | None
    amends_report_date: dt.date | None
    superseded_on: dt.date | None


def parse_amendment(note: str | None) -> Amendment:
    """異動情形：`本單為變更115/08/14 之申報`、`本單已於115/08/18 申報變更`。其餘保留原文不解析。"""
    text = clean_text(note)
    if not text:
        return Amendment(None, None, None)
    amends = re.search(r"本單為變更\s*(\d{2,3}/\d{1,2}/\d{1,2})", text)
    superseded = re.search(r"本單已於\s*(\d{2,3}/\d{1,2}/\d{1,2})", text)
    return Amendment(
        text,
        parse_roc_any(amends.group(1)) if amends else None,
        parse_roc_any(superseded.group(1)) if superseded else None,
    )


def _transfer_row(
    *, symbol: str, market: str, source: str, report_date: dt.date, role, name, method,
    shares, max_intraday, transferee, cur_own, cur_trust, plan_own, plan_trust,
    after_own, after_trust, period, note=None, unfinished=None, t: Thresholds | None = None,
) -> dict:
    start, end = parse_period(period)
    method_text = clean_text(method) or None
    amendment = parse_amendment(note)
    row = {
        "symbol": symbol, "market": market, "source": source,
        "data_date": report_date, "available_at": transfer_available_at(report_date, t),
        "reporter_role": clean_text(role), "reporter_name": clean_text(name),
        "transfer_method": method_text, "method_category": method_category(method_text),
        "transfer_shares": parse_shares(shares), "max_intraday_shares": parse_shares(max_intraday),
        "transferee": clean_text(transferee) or None,
        "current_own_shares": parse_shares(cur_own), "current_trust_shares": parse_shares(cur_trust),
        "planned_own_shares": parse_shares(plan_own), "planned_trust_shares": parse_shares(plan_trust),
        "after_own_shares": parse_shares(after_own), "after_trust_shares": parse_shares(after_trust),
        "effective_start": start, "effective_end": end,
        "amendment_note": amendment.note,
        "amends_report_date": amendment.amends_report_date,
        "superseded_on": amendment.superseded_on,
        "unfinished_flag": clean_text(unfinished) or None,
    }
    row["row_hash"] = row_hash(row)
    return row


def collapse_duplicates(rows: list[dict]) -> list[dict]:
    """同一批內 row_hash 相同者合併為一列並記 duplicate_count（實測來源會重複顯示同一筆）。"""
    merged: dict[tuple, dict] = {}
    for r in rows:
        key = (r["symbol"], r["data_date"], r["row_hash"])
        if key in merged:
            merged[key] = {**merged[key], "duplicate_count": merged[key]["duplicate_count"] + 1}
        else:
            merged[key] = {**r, "duplicate_count": 1}
    return list(merged.values())


@dataclass(frozen=True)
class TransferBatch:
    report_date: dt.date | None  # 此批涵蓋的申報日（寫入涵蓋紀錄用）
    rows: list[dict]


def parse_openapi_transfers(
    records: list[dict], market: str, t: Thresholds | None = None
) -> TransferBatch:
    """OpenAPI 持股轉讓日報表。無申報日回傳一列空白欄位（實測），仍可由出表日期得到涵蓋日。"""
    report_dates = {parse_roc_any(_pick(r, "出表日期", "Date")) for r in records} - {None}
    if len(report_dates) > 1:
        raise MopsPageError(f"轉讓日報表含多個出表日期：{sorted(report_dates)}")
    report_date = next(iter(report_dates), None)
    rows: list[dict] = []
    for r in records:
        sym = clean_text(_pick(r, "公司代號", "SecuritiesCompanyCode"))
        if not is_stock_symbol(sym) or report_date is None:
            continue
        rows.append(_transfer_row(
            symbol=sym, market=market, source=SOURCE_OPENAPI, report_date=report_date,
            role=_pick(r, "申報人身分", "申請人身分"), name=_pick(r, "姓名"),
            method=_pick(r, "預定轉讓方式及股數-轉讓方式"),
            shares=_pick(r, "預定轉讓方式及股數-轉讓股數"),
            max_intraday=_pick(r, "每日於盤中交易最大得轉讓股數"), transferee=_pick(r, "受讓人"),
            cur_own=_pick(r, "目前持有股數-自有持股"),
            cur_trust=_pick(r, "目前持有股數-保留運用決定權信託股數"),
            plan_own=_pick(r, "預定轉讓總股數-自有持股"),
            plan_trust=_pick(r, "預定轉讓總股數-保留運用決定權信託股數"),
            after_own=_pick(r, "預定轉讓後持股-自有持股"),
            after_trust=_pick(r, "預定轉讓後持股-保留運用決定權信託股數"),
            period=_pick(r, "有效轉讓期間"), t=t,
        ))
    return TransferBatch(report_date, collapse_duplicates(rows))


_EMPTY_TRANSFER = "無持股轉讓之情形"


def parse_transfer_page(
    html: str, market: str, report_date: dt.date, t: Thresholds | None = None
) -> TransferBatch:
    """MOPS ajax_t56sb12（單日單市場）。`無持股轉讓之情形` = 明確零筆（仍算涵蓋）。

    既無資料表也無零筆字樣 → MopsPageError（不可把異常頁當成「當日沒有申報」）。
    """
    rows_html = re.findall(r"<tr class='(?:odd|even)'>(.*?)</tr>", html, flags=re.S | re.I)
    if not rows_html:
        if _EMPTY_TRANSFER in html:
            return TransferBatch(report_date, [])
        raise MopsPageError(f"{report_date} {market} 轉讓申報頁無資料表也無零筆字樣")
    # 上市頁 18 欄；上櫃頁（實測 OY）沒有「是否申報持股未完成轉讓」欄，只有 17 欄
    has_unfinished = "是否申報持" in html
    expected = 18 if has_unfinished else 17
    rows: list[dict] = []
    for row in rows_html:
        c = _cells(row)
        if len(c) < expected:
            raise MopsPageError(f"{report_date} {market} 轉讓申報列欄數 {len(c)} < {expected}")
        d = parse_roc_any(c[1])
        sym = c[2]
        if d != report_date:
            raise MopsPageError(f"申報日期 {c[1]} 與查詢日 {report_date} 不符")
        if not is_stock_symbol(sym):
            continue
        rows.append(_transfer_row(
            symbol=sym, market=market, source=SOURCE_WEB, report_date=d,
            role=c[4], name=c[5], method=c[6], shares=c[7], max_intraday=c[8],
            transferee=c[9], cur_own=c[10], cur_trust=c[11], plan_own=c[12],
            plan_trust=c[13], after_own=c[14], after_trust=c[15], period=c[16],
            note=c[0], unfinished=c[17] if has_unfinished else None, t=t,
        ))
    return TransferBatch(report_date, collapse_duplicates(rows))
