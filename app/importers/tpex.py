"""TPEx 上櫃盤後資料解析（純函式:raw JSON → 可 upsert 的 record dict）。

- 行情:afterTrading/dailyQuotes(欄位以標題定位;「收盤」在「開盤」之前)
- 三大法人:insti/dailyTrade(欄名大量重複,用固定位置;分組已實測驗證:
  [2-4]外資不含自營 [5-7]外資自營 [8-10]外資合計 [11-13]投信
  [14-16]自營自行 [17-19]自營避險 [20-22]自營合計 [23]三大法人合計)
- 融資融券:margin/balance(欄名唯一,以標題定位;單位張)

輸出 record 形狀與 importers.twse 一致(market='TPEx'),共用 service upsert。
"""
from __future__ import annotations

import datetime as dt
import re

from app.importers.base import (
    availability_for,
    col_index,
    is_stock_symbol,
    parse_float,
    parse_int,
    parse_roc_compact_date,
    parse_roc_date,
)


def _first_table(raw: dict) -> dict | None:
    for t in raw.get("tables", []):
        if t.get("data"):
            return t
    return None


def parse_ohlcv(raw: dict, data_date: dt.date) -> tuple[list[dict], list[dict]]:
    """回傳 (stocks, prices)。stocks 供 Stock 主檔 upsert(market='TPEx')。"""
    table = _first_table(raw)
    if table is None:
        return [], []
    f = table["fields"]
    i_sym = col_index(f, "代號")
    i_name = col_index(f, "名稱")
    i_close = col_index(f, "收盤")
    i_open = col_index(f, "開盤")
    i_high = col_index(f, "最高")
    i_low = col_index(f, "最低")
    i_vol = col_index(f, "成交股數")
    i_turn = col_index(f, "成交金額(元)", "成交金額")
    if None in (i_sym, i_close, i_open):
        return [], []
    av = availability_for(data_date)

    stocks: list[dict] = []
    prices: list[dict] = []
    for row in table.get("data", []):
        sym = str(row[i_sym]).strip()
        if not is_stock_symbol(sym):
            continue
        close = parse_float(row[i_close])
        if close is None:  # 無成交(全部 '--')的掛牌股跳過
            continue
        stocks.append(
            {"symbol": sym, "name": str(row[i_name]).strip(), "market": "TPEx"}
        )
        prices.append(
            {
                "symbol": sym,
                "data_date": data_date,
                "available_at": av,
                "open": parse_float(row[i_open]),
                "high": parse_float(row[i_high]),
                "low": parse_float(row[i_low]),
                "close": close,
                "volume": parse_int(row[i_vol]),
                "turnover": parse_float(row[i_turn]),
            }
        )
    return stocks, prices


# insti/dailyTrade 固定位置(欄名重複;分組經實測加總驗證)
_I = {"sym": 0, "foreign_total_net": 10, "trust_net": 13,
      "dealer_self_net": 16, "dealer_hedge_net": 19}


def parse_institutional(raw: dict, data_date: dt.date) -> list[dict]:
    table = _first_table(raw)
    if table is None:
        return []
    av = availability_for(data_date)
    out: list[dict] = []
    for row in table.get("data", []):
        if len(row) <= _I["dealer_hedge_net"]:
            continue
        sym = str(row[_I["sym"]]).strip()
        if not is_stock_symbol(sym):
            continue
        out.append(
            {
                "symbol": sym,
                "data_date": data_date,
                "available_at": av,
                # 外資合計已含外資自營(對齊 TWSE parser 的合併邏輯)
                "foreign_net": parse_int(row[_I["foreign_total_net"]]),
                "trust_net": parse_int(row[_I["trust_net"]]),
                "dealer_self_net": parse_int(row[_I["dealer_self_net"]]),
                "dealer_hedge_net": parse_int(row[_I["dealer_hedge_net"]]),
            }
        )
    return out


def parse_margin(raw: dict, data_date: dt.date) -> list[dict]:
    table = _first_table(raw)
    if table is None:
        return []
    f = table["fields"]
    i_sym = col_index(f, "代號")
    i_mbuy = col_index(f, "資買")
    i_msell = col_index(f, "資賣")
    i_mbal = col_index(f, "資餘額")
    i_ssell = col_index(f, "券賣")
    i_scover = col_index(f, "券買")
    i_sbal = col_index(f, "券餘額")
    if None in (i_sym, i_mbal, i_sbal):
        return []
    av = availability_for(data_date)
    out: list[dict] = []
    for row in table.get("data", []):
        sym = str(row[i_sym]).strip()
        if not is_stock_symbol(sym):
            continue
        out.append(
            {
                "symbol": sym,
                "data_date": data_date,
                "available_at": av,
                "margin_buy": parse_int(row[i_mbuy]),
                "margin_sell": parse_int(row[i_msell]),
                "margin_balance": parse_int(row[i_mbal]),
                "short_sell": parse_int(row[i_ssell]),
                "short_cover": parse_int(row[i_scover]),
                "short_balance": parse_int(row[i_sbal]),
            }
        )
    return out


# --- 公司行動（bulletin/*）：與 TWSE 共用 CorporateAction，輸出形狀一致 ---

# TPEx 的「權/息」用中文全稱，正規化成與 TWSE 一致的 權/息/權息。
_EX_KIND = {"除權": "權", "除息": "息", "除權息": "權息"}

# 詳細資料是 HTML 表格；換股率／換發股數只存在其中，比 1/adj_factor 精確
# （現金減資有「每股退還股款」，1/adj_factor 會偏差，見 revivt 的璟德）。
_PAR_RATIO_RE = re.compile(r"變更股票面額換股率[:：]\s*</th>\s*<td>\s*([0-9.]+)")
_REDUCE_SHARES_RE = re.compile(r"每壹仟股換發新股票[:：]\s*</th>\s*<td>\s*([0-9.]+)")


def parse_ex_dividend(raw: dict) -> list[dict]:
    """exDailyQ 除權除息計算結果表 → CorporateAction。

    adj_factor = 除權息參考價 / 除權息前收盤價；
    share_factor = 1 + 每仟股無償配股/1000（同表即有，故 TPEx 的量因子歷史可回補，
    不像 TWSE 需依賴只回未來的 TWT48U 預告表）。
    """
    table = _first_table(raw)
    if table is None:
        return []
    f = table["fields"]
    i_date = col_index(f, "除權息日期")
    i_sym = col_index(f, "代號")
    i_prev = col_index(f, "除權息前收盤價")
    i_ref = col_index(f, "除權息參考價")
    i_val = col_index(f, "權值+息值")
    i_kind = col_index(f, "權/息")
    i_stk = col_index(f, "每仟股無償配股")
    if i_date is None or i_sym is None:
        return []

    out: list[dict] = []
    for row in table.get("data", []):
        sym = str(row[i_sym]).strip()
        if not is_stock_symbol(sym):
            continue
        d = parse_roc_date(row[i_date])
        if d is None:
            continue
        prev = parse_float(row[i_prev]) if i_prev is not None else None
        ref = parse_float(row[i_ref]) if i_ref is not None else None
        adj = ref / prev if prev and prev > 0 and ref is not None else None
        stk = parse_float(row[i_stk]) if i_stk is not None else None
        share = 1 + stk / 1000 if stk is not None else None
        out.append(
            {
                "symbol": sym,
                "data_date": d,
                "available_at": availability_for(d),
                "kind": _EX_KIND.get(str(row[i_kind]).strip(), str(row[i_kind]).strip())
                if i_kind is not None
                else "",
                "prev_close": prev,
                "reference_price": ref,
                "value": parse_float(row[i_val]) if i_val is not None else None,
                "adj_factor": round(adj, 8) if adj is not None else None,
                "share_factor": round(share, 8) if share is not None else None,
            }
        )
    return out


def parse_resume_reference(raw: dict, kind: str) -> list[dict]:
    """pvChgRslt（面額變更）/ revivt（減資）→ CorporateAction。

    兩表同構：恢復買賣日期（民國緊湊 1150309）、代號、最後交易日之收盤價格、參考價。
    share_factor 取自「詳細資料」的換股率／每壹仟股換發新股票（精確）；取不到才退回
    1/adj_factor（純股數變動時等價，現金減資會有偏差故僅作後備）。
    """
    table = _first_table(raw)
    if table is None:
        return []
    f = table["fields"]
    i_date = col_index(f, "恢復買賣日期")
    i_sym = col_index(f, "證券代號", "股票代號")
    i_prev = col_index(f, "最後交易日之收盤價格")
    i_ref = col_index(f, "恢復買賣開始參考價", "減資恢復買賣開始日參考價格")
    i_detail = col_index(f, "詳細資料")
    if i_date is None or i_sym is None:
        return []

    out: list[dict] = []
    for row in table.get("data", []):
        sym = str(row[i_sym]).strip()
        if not is_stock_symbol(sym):
            continue
        d = parse_roc_compact_date(row[i_date])
        if d is None:
            continue
        prev = parse_float(row[i_prev]) if i_prev is not None else None
        ref = parse_float(row[i_ref]) if i_ref is not None else None
        adj = ref / prev if prev and prev > 0 and ref is not None else None

        share: float | None = None
        detail = str(row[i_detail]) if i_detail is not None else ""
        if m := _PAR_RATIO_RE.search(detail):          # 面額變更：1 舊股→N 新股
            share = parse_float(m.group(1))
        elif m := _REDUCE_SHARES_RE.search(detail):    # 減資：每仟股換發 N 股
            v = parse_float(m.group(1))
            share = v / 1000 if v is not None else None
        if share is None and adj:                      # 後備：純股數變動時等價
            share = 1 / adj

        out.append(
            {
                "symbol": sym,
                "data_date": d,
                "available_at": availability_for(d),
                "kind": kind,
                "prev_close": prev,
                "reference_price": ref,
                "value": None,
                "adj_factor": round(adj, 8) if adj is not None else None,
                "share_factor": round(share, 8) if share is not None else None,
            }
        )
    return out
