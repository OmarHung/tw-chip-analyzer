"""TAIFEX 期貨每日行情 parser（CSV → FuturesDaily 列）。"""
from __future__ import annotations

import csv
import datetime as dt
import io

from app.importers.base import availability_for, parse_float, parse_int

# 只取日盤。「盤後」(夜盤)跨到隔日凌晨才收，EOD 當下抓到的是半截資料，
# 混進來會讓「收盤價」語意不明；要用夜盤時請另外落一份、勿改這裡的過濾。
SESSION_REGULAR = "一般"


def _parse_date(s: str) -> dt.date | None:
    """期交所西元日期 '2026/09/16' → date。"""
    try:
        y, m, d = (int(p) for p in str(s).strip().split("/"))
        return dt.date(y, m, d)
    except (ValueError, TypeError):
        return None


def _parse_pct(s: str) -> float | None:
    """'0.84%' → 0.0084（小數，與全站 change_pct 同尺度）。"""
    v = parse_float(str(s).strip().rstrip("%"))
    return None if v is None else round(v / 100.0, 6)


def parse_futures_daily(csv_text: str, contract: str = "TX") -> list[dict]:
    """futDataDown CSV → 每日每月份契約一列（僅日盤、僅單式契約）。

    排除兩類列：
    - 交易時段為「盤後」（見 SESSION_REGULAR）。
    - 價差契約（到期月份形如 '202609/202610'），其報價是價差不是指數點位。
    """
    out: list[dict] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for raw in reader:
        row = {str(k).strip(): (v or "") for k, v in raw.items() if k is not None}
        if row.get("交易時段", "").strip() != SESSION_REGULAR:
            continue
        month = row.get("到期月份(週別)", "").strip()
        if not month or "/" in month:
            continue
        d = _parse_date(row.get("交易日期", ""))
        close = parse_float(row.get("收盤價"))
        if d is None or close is None:
            continue
        out.append(
            {
                "data_date": d,
                "available_at": availability_for(d),
                "contract": (row.get("契約", "").strip() or contract),
                "contract_month": month,
                "open": parse_float(row.get("開盤價")),
                "high": parse_float(row.get("最高價")),
                "low": parse_float(row.get("最低價")),
                "close": close,
                "change": parse_float(row.get("漲跌價")),
                "change_pct": _parse_pct(row.get("漲跌%", "")),
                "volume": parse_int(row.get("成交量")),
                "settlement_price": parse_float(row.get("結算價")),
                "open_interest": parse_int(row.get("未沖銷契約數")),
            }
        )
    return out
