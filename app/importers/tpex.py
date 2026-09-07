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

from app.importers.base import (
    availability_for,
    col_index,
    is_stock_symbol,
    parse_float,
    parse_int,
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
