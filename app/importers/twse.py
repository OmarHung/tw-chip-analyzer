"""TWSE 盤後資料解析（純函式：raw JSON → 可 upsert 的 record dict）。

- OHLCV：MI_INDEX（type=ALLBUT0999）
- 三大法人：T86
- 融資融券：MI_MARGN（selectType=STOCK）
- 借券 SBL：TWT93U（信用額度總量管制餘額表）

欄位以標題名稱定位（避免順序變動）；MI_MARGN / TWT93U 因欄名重複改用固定位置。
"""
from __future__ import annotations

import datetime as dt

from app.importers.base import (
    availability_for,
    col_index,
    is_stock_symbol,
    parse_float,
    parse_int,
    parse_roc_cjk_date,
    parse_roc_date,
)


def _ohlcv_table(raw: dict) -> dict | None:
    for t in raw.get("tables", []):
        f = t.get("fields") or []
        if "收盤價" in [str(x).strip() for x in f] and "證券代號" in [
            str(x).strip() for x in f
        ]:
            return t
    return None


def parse_ohlcv(raw: dict, data_date: dt.date) -> tuple[list[dict], list[dict]]:
    """回傳 (stocks, prices)。stocks 供 Stock 主檔 upsert。"""
    table = _ohlcv_table(raw)
    if table is None:
        return [], []
    f = table["fields"]
    i_sym = col_index(f, "證券代號")
    i_name = col_index(f, "證券名稱")
    i_vol = col_index(f, "成交股數")
    i_turn = col_index(f, "成交金額")
    i_open = col_index(f, "開盤價")
    i_high = col_index(f, "最高價")
    i_low = col_index(f, "最低價")
    i_close = col_index(f, "收盤價")
    av = availability_for(data_date)

    stocks: list[dict] = []
    prices: list[dict] = []
    for row in table.get("data", []):
        sym = str(row[i_sym]).strip()
        if not is_stock_symbol(sym):
            continue
        stocks.append(
            {"symbol": sym, "name": str(row[i_name]).strip(), "market": "TWSE"}
        )
        prices.append(
            {
                "symbol": sym,
                "data_date": data_date,
                "available_at": av,
                "open": parse_float(row[i_open]),
                "high": parse_float(row[i_high]),
                "low": parse_float(row[i_low]),
                "close": parse_float(row[i_close]),
                "volume": parse_int(row[i_vol]),
                "turnover": parse_float(row[i_turn]),
            }
        )
    return stocks, prices


def parse_institutional(raw: dict, data_date: dt.date) -> list[dict]:
    f = raw.get("fields") or []
    i_sym = col_index(f, "證券代號")
    i_foreign = col_index(f, "外陸資買賣超股數(不含外資自營商)", "外資買賣超股數")
    i_foreign_dealer = col_index(f, "外資自營商買賣超股數")
    i_trust = col_index(f, "投信買賣超股數")
    i_dealer_self = col_index(f, "自營商買賣超股數(自行買賣)")
    i_dealer_hedge = col_index(f, "自營商買賣超股數(避險)")
    av = availability_for(data_date)

    out: list[dict] = []
    for row in raw.get("data", []):
        sym = str(row[i_sym]).strip()
        if not is_stock_symbol(sym):
            continue
        foreign = parse_int(row[i_foreign]) or 0
        if i_foreign_dealer is not None:
            foreign += parse_int(row[i_foreign_dealer]) or 0
        out.append(
            {
                "symbol": sym,
                "data_date": data_date,
                "available_at": av,
                "foreign_net": foreign,
                "trust_net": parse_int(row[i_trust]),
                "dealer_self_net": parse_int(row[i_dealer_self])
                if i_dealer_self is not None
                else None,
                "dealer_hedge_net": parse_int(row[i_dealer_hedge])
                if i_dealer_hedge is not None
                else None,
            }
        )
    return out


def parse_index(raw: dict) -> list[dict]:
    """FMTQIK → 每日 TAIEX 收盤指數（民國日期）。"""
    f = raw.get("fields") or []
    i_date = col_index(f, "日期")
    i_close = col_index(f, "發行量加權股價指數")
    i_turn = col_index(f, "成交金額")
    if i_date is None or i_close is None:
        return []
    out: list[dict] = []
    for row in raw.get("data", []):
        d = parse_roc_date(row[i_date])
        if d is None:
            continue
        out.append(
            {
                "data_date": d,
                "available_at": availability_for(d),
                "taiex_close": parse_float(row[i_close]),
                "turnover": parse_float(row[i_turn]) if i_turn is not None else None,
            }
        )
    return out


def parse_ex_dividend(raw: dict) -> list[dict]:
    """TWT49U → 除權除息事件。data_date 取自列內「資料日期」（區間查詢每列各自帶日期）。

    還原因子 adj_factor = 除權息參考價 / 除權息前收盤價（把 data_date 前的價乘上它，
    使報酬/MA/ATR 連續）。缺任一價或前收<=0 則 adj_factor 記 None。

    **不產出 share_factor**：本報表沒有任何欄位能分離出「配股率」。adj_factor 混了不改
    股數的現金股利；「減除股利參考價」減掉的是股利（含無償配股）、反而只剩現金增資效果
    （實測：無償配股 7.1% 的 2442 比值為 1.0，純現增的 6533 卻是 1.032），不可拿來推。
    配股率改由 TWT48U 的無償配股率提供（見 parse_ex_rights_forecast），故此處連 key 都
    不放，讓 upsert 不會覆蓋掉 TWT48U 已寫入的 share_factor。
    """
    f = raw.get("fields") or []
    i_date = col_index(f, "資料日期")
    i_sym = col_index(f, "股票代號")
    i_prev = col_index(f, "除權息前收盤價")
    i_ref = col_index(f, "除權息參考價")
    i_val = col_index(f, "權值+息值")
    i_kind = col_index(f, "權/息")
    if i_date is None or i_sym is None:
        return []

    out: list[dict] = []
    for row in raw.get("data", []):
        sym = str(row[i_sym]).strip()
        if not is_stock_symbol(sym):
            continue
        d = parse_roc_cjk_date(row[i_date])
        if d is None:
            continue
        prev = parse_float(row[i_prev]) if i_prev is not None else None
        ref = parse_float(row[i_ref]) if i_ref is not None else None
        adj = ref / prev if prev and prev > 0 and ref is not None else None
        out.append(
            {
                "symbol": sym,
                "data_date": d,
                "available_at": availability_for(d),
                "kind": str(row[i_kind]).strip() if i_kind is not None else "",
                "prev_close": prev,
                "reference_price": ref,
                "value": parse_float(row[i_val]) if i_val is not None else None,
                "adj_factor": round(adj, 8) if adj is not None else None,
            }
        )
    return out


def parse_ex_rights_forecast(raw: dict) -> list[dict]:
    """TWT48U 除權除息預告表 → 只補 CorporateAction 的 share_factor（量還原因子）。

    share_factor = 1 + 無償配股率：只有無償配股才讓既有股東的 1 股變成 1+r 股。現金增資
    不會（既有股數不變，要自己掏錢認購），故不計入。已驗證
    `參考價 = (前收 - 現金股利) / (1 + 無償配股率)` 在無現增的樣本上完全吻合。

    回傳的 row 刻意只帶 share_factor（不帶 prev_close/adj_factor 等），因為預告表在除權息
    「前」發布、當時還沒有前收與參考價；等 TWT49U 於當日補上價格欄位即成完整一列。
    無償配股率為 0 者略過（不調量）。
    """
    f = raw.get("fields") or []
    i_date = col_index(f, "除權除息日期")
    i_sym = col_index(f, "股票代號")
    i_kind = col_index(f, "除權息")
    i_free = col_index(f, "無償配股率")
    if i_date is None or i_sym is None or i_free is None:
        return []

    out: list[dict] = []
    for row in raw.get("data", []):
        sym = str(row[i_sym]).strip()
        if not is_stock_symbol(sym):
            continue
        d = parse_roc_cjk_date(row[i_date])
        free = parse_float(row[i_free])
        if d is None or not free or free <= 0:
            continue
        out.append(
            {
                "symbol": sym,
                "data_date": d,
                # 預告表在除權息日之前就公布，取得當下即可用（不必等到除權息日）。
                "available_at": availability_for(dt.date.today()),
                "kind": str(row[i_kind]).strip() if i_kind is not None else "",
                "share_factor": round(1 + free, 8),
            }
        )
    return out


def parse_resume_reference(raw: dict, kind: str) -> list[dict]:
    """TWTB8U（面額變更）/ TWTAUU（減資）→ 除權除息以外的價格斷點事件。

    兩報表欄位一致：恢復買賣日期（民國斜線）、股票代號、停止買賣前收盤價格、恢復買賣參考價。
    data_date = 恢復買賣日期（恢復交易首日 = 斷點日）；adj_factor = 參考價 / 前收
    （面額變更/拆股 <1；減資 >1）。與 TWT49U 共用 CorporateAction。

    量因子只在**純股數變動**時等於 1/adj_factor（價跌幾倍、股數就漲幾倍）：面額變更、
    彌補虧損減資皆屬之。**現金減資（減資原因含「退還股款」）不是**——TWTAUU 自載的公式
    `參考價 =（前收 - 息值 - 每股退還股款）/ 減資換股率` 說明參考價已先扣掉退還的現金，
    1/adj_factor 會系統性高估留存股數（實測 6176 瑞儀：1/adj = 0.774，真實換股率 0.75）。
    故現金減資此處記 share_factor=None，改由 TWTAVU 減資預告表提供精確換股率
    （見 parse_capital_reduction_forecast）；`import_capital_reduction` 亦不在衝突時
    更新 share_factor，避免把預告表寫入的正確值洗掉。
    """
    f = raw.get("fields") or []
    i_date = col_index(f, "恢復買賣日期")
    i_sym = col_index(f, "股票代號")
    i_prev = col_index(f, "停止買賣前收盤價格")
    i_ref = col_index(f, "恢復買賣參考價")
    i_reason = col_index(f, "減資原因")  # TWTB8U 無此欄（面額變更必為純股數變動）
    if i_date is None or i_sym is None:
        return []

    out: list[dict] = []
    for row in raw.get("data", []):
        sym = str(row[i_sym]).strip()
        if not is_stock_symbol(sym):
            continue
        d = parse_roc_date(row[i_date])
        if d is None:
            continue
        prev = parse_float(row[i_prev]) if i_prev is not None else None
        ref = parse_float(row[i_ref]) if i_ref is not None else None
        adj = ref / prev if prev and prev > 0 and ref is not None else None
        reason = str(row[i_reason]).strip() if i_reason is not None else ""
        cash_back = "退還股款" in reason
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
                "share_factor": (
                    None if cash_back or not adj else round(1 / adj, 8)
                ),
            }
        )
    return out


def parse_capital_reduction_forecast(raw: dict) -> list[dict]:
    """TWTAVU 減資預告表 → 只補 CorporateAction 的 share_factor（量還原因子）。

    share_factor = 減資換股率（＝減資後發行股數/減資前發行股數，1 舊股→幾新股），對
    彌補虧損與現金減資皆精確；現金減資更是唯一正確來源（TWTAUU 的 1/adj_factor 會偏差）。
    data_date 取「恢復買賣日期」，與 TWTAUU 同鍵，兩來源寫同一列（價因子由 TWTAUU 於
    恢復買賣日補上）。與 TWT48U 同樣是預告表，故 available_at 記今日、歷史補不回來。
    """
    f = raw.get("fields") or []
    i_date = col_index(f, "恢復買賣日期")
    i_sym = col_index(f, "股票代號")
    i_ratio = col_index(f, "減資換股率")
    if i_date is None or i_sym is None or i_ratio is None:
        return []

    out: list[dict] = []
    for row in raw.get("data", []):
        sym = str(row[i_sym]).strip()
        if not is_stock_symbol(sym):
            continue
        d = parse_roc_date(row[i_date])
        ratio = parse_float(row[i_ratio])
        if d is None or not ratio or ratio <= 0:
            continue
        out.append(
            {
                "symbol": sym,
                "data_date": d,
                # 預告表在恢復買賣日之前公布，取得當下即可用。
                "available_at": availability_for(dt.date.today()),
                "kind": "減資",
                "share_factor": round(ratio, 8),
            }
        )
    return out


def _margin_table(raw: dict) -> dict | None:
    for t in raw.get("tables", []):
        f = t.get("fields") or []
        if "資券互抵" in [str(x).strip() for x in f]:
            return t
    return None


# MI_MARGN 固定欄位位置（欄名重複，見 docs 註記）
_M = {
    "sym": 0, "margin_buy": 2, "margin_sell": 3, "margin_balance": 6,
    "short_sell": 9, "short_cover": 10, "short_balance": 12,
}


def parse_margin(raw: dict, data_date: dt.date) -> list[dict]:
    table = _margin_table(raw)
    if table is None:
        return []
    av = availability_for(data_date)
    out: list[dict] = []
    for row in table.get("data", []):
        sym = str(row[_M["sym"]]).strip()
        if not is_stock_symbol(sym):
            continue
        out.append(
            {
                "symbol": sym,
                "data_date": data_date,
                "available_at": av,
                "margin_buy": parse_int(row[_M["margin_buy"]]),
                "margin_sell": parse_int(row[_M["margin_sell"]]),
                "margin_balance": parse_int(row[_M["margin_balance"]]),
                "short_sell": parse_int(row[_M["short_sell"]]),
                "short_cover": parse_int(row[_M["short_cover"]]),
                "short_balance": parse_int(row[_M["short_balance"]]),
            }
        )
    return out


# TWT93U 信用額度總量管制餘額表：融券段(2-7) + 借券段(8-14)，欄名重複用固定位置。
# 借券段：[9]當日賣出 [10]當日還券 [12]當日餘額。單位股數。
_SBL = {"sym": 0, "sbl_short_sell": 9, "sbl_return": 10, "sbl_balance": 12}


def parse_sbl(raw: dict, data_date: dt.date) -> list[dict]:
    """TWT93U → 借券（SBL）每檔賣出/還券/餘額。資料為 flat（raw['data']）。"""
    av = availability_for(data_date)
    out: list[dict] = []
    for row in raw.get("data", []):
        if len(row) <= _SBL["sbl_balance"]:
            continue
        sym = str(row[_SBL["sym"]]).strip()
        if not is_stock_symbol(sym):
            continue
        out.append(
            {
                "symbol": sym,
                "data_date": data_date,
                "available_at": av,
                "sbl_short_sell": parse_int(row[_SBL["sbl_short_sell"]]),
                "sbl_return": parse_int(row[_SBL["sbl_return"]]),
                "sbl_balance": parse_int(row[_SBL["sbl_balance"]]),
            }
        )
    return out
