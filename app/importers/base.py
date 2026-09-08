"""Importer 共用解析工具。

TWSE/TPEx 回傳的數字多為含千分位的字串（"1,262,000"），
未成交或無資料常見 "--"、""、"X"、"---"。一律安全轉換。
"""
from __future__ import annotations

import datetime as dt

_NULL_TOKENS = {"", "--", "---", "x", "X", "N/A", "n/a", "null", "None"}


def parse_int(value) -> int | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if s in _NULL_TOKENS:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_float(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if s in _NULL_TOKENS:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def col_index(fields: list[str], *names: str) -> int | None:
    """依欄位標題名稱找 index（容忍前後空白）。找不到回 None。"""
    stripped = [str(f).strip() for f in fields]
    for name in names:
        if name in stripped:
            return stripped.index(name)
    return None


def twse_date(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def parse_roc_date(s: str) -> dt.date | None:
    """民國日期 '115/09/04' → date(2026, 9, 4)。"""
    try:
        parts = str(s).strip().split("/")
        if len(parts) != 3:
            return None
        y, m, d = int(parts[0]) + 1911, int(parts[1]), int(parts[2])
        return dt.date(y, m, d)
    except (ValueError, TypeError):
        return None


def parse_roc_compact_date(s: str) -> dt.date | None:
    """民國緊湊日期 '1150309' → date(2026, 3, 9)（TPEx bulletin 恢復買賣日期用）。"""
    t = str(s).strip()
    if len(t) != 7 or not t.isdigit():
        return None
    return parse_roc_date(f"{t[:3]}/{t[3:5]}/{t[5:]}")


def parse_roc_cjk_date(s: str) -> dt.date | None:
    """民國日期（含年月日字元）'115年09月09日' → date(2026, 9, 9)。"""
    return parse_roc_date(
        str(s).strip().replace("年", "/").replace("月", "/").rstrip("日")
    )


def is_stock_symbol(symbol: str) -> bool:
    """只保留 4 位數普通股（排除 ETF/權證/00 開頭等）。第一版聚焦一般個股。"""
    s = symbol.strip()
    return len(s) == 4 and s.isdigit() and not s.startswith("00")


def availability_for(data_date: dt.date, hour: int = 15) -> dt.datetime:
    """資料可用時點（盤後）。用於 look-ahead 防護。"""
    return dt.datetime(data_date.year, data_date.month, data_date.day, hour, 0)
