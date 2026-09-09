"""TDCC 集保戶股權分散解析（openapi 1-5）。

回應為扁平 list，每檔 17 個持股分級：1-15 為分佈級距，16 差異調整，17 合計。
代號常帶尾隨空白，需 strip。級距→散戶/中戶/大戶/超大戶 依 config 門檻分類。
"""
from __future__ import annotations

import datetime as dt
import re

from app.core.config import get_thresholds
from app.importers.base import is_stock_symbol, parse_float, parse_int

_DATE_KEY = "﻿資料日期"  # 欄名帶 BOM

# 標準 TDCC 級距上界（股數）；level 15 無上界（1,000,001+）
LEVEL_UPPER_SHARES: dict[int, int | None] = {
    1: 999, 2: 5000, 3: 10000, 4: 15000, 5: 20000, 6: 30000, 7: 40000,
    8: 50000, 9: 100000, 10: 200000, 11: 400000, 12: 600000, 13: 800000,
    14: 1_000_000, 15: None,
}


def _bucket(level: int, retail_max: int, large_min: int, super_min: int) -> str:
    """依 config 門檻（股）將級距分到 retail/medium/large/super_large。"""
    upper = LEVEL_UPPER_SHARES.get(level)
    if upper is None:  # level 15
        return "super_large"
    if upper <= retail_max:
        return "retail"
    if upper <= large_min:
        return "medium"
    if upper <= super_min:
        return "large"
    return "super_large"


def parse_distribution(
    records: list[dict],
) -> tuple[dt.date | None, list[dict], list[dict]]:
    """回傳 (data_date, weekly_rows, summary_rows)。"""
    if not records:
        return None, [], []

    t = get_thresholds().tdcc
    retail_max = t.get("retail_max_lots", 50) * 1000
    large_min = t.get("large_min_lots", 400) * 1000
    super_min = t.get("super_large_min_lots", 1000) * 1000

    raw_date = str(records[0].get(_DATE_KEY, "")).strip()
    try:
        data_date = dt.datetime.strptime(raw_date, "%Y%m%d").date()
    except ValueError:
        return None, [], []
    # TDCC 週資料實際揭露落後數日；為避免 look-ahead，於揭露日才可用。
    # 第一版：openapi 僅當週快照，available_at 設為快照日盤後（見 docs 註記）。
    available_at = dt.datetime(data_date.year, data_date.month, data_date.day, 18, 0)

    by_symbol: dict[str, list[dict]] = {}
    for r in records:
        sym = str(r.get("證券代號", "")).strip()
        if not is_stock_symbol(sym):
            continue
        by_symbol.setdefault(sym, []).append(r)

    weekly_rows: list[dict] = []
    summary_rows: list[dict] = []
    for sym, rows in by_symbol.items():
        buckets = {"retail": 0.0, "medium": 0.0, "large": 0.0, "super_large": 0.0}
        holder_count_total = None
        for r in rows:
            level = parse_int(r.get("持股分級"))
            if level is None:
                continue
            ratio = parse_float(r.get("占集保庫存數比例%")) or 0.0
            holders = parse_int(r.get("人數"))
            shares = parse_int(r.get("股數"))
            if level == 17:  # 合計
                holder_count_total = holders
                continue
            if level == 16:  # 差異數調整
                continue
            if 1 <= level <= 15:
                weekly_rows.append(
                    {
                        "symbol": sym, "data_date": data_date,
                        "available_at": available_at, "level": level,
                        "holder_count": holders, "shares": shares, "ratio": ratio,
                    }
                )
                buckets[_bucket(level, retail_max, large_min, super_min)] += ratio

        summary_rows.append(
            {
                "symbol": sym, "data_date": data_date, "available_at": available_at,
                "retail_ratio": round(buckets["retail"], 6),
                "medium_ratio": round(buckets["medium"], 6),
                "large_ratio": round(buckets["large"], 6),
                "super_large_ratio": round(buckets["super_large"], 6),
                "holder_count": holder_count_total,
            }
        )
    return data_date, weekly_rows, summary_rows


def parse_stock_page(html: str, symbol: str, data_date: dt.date) -> list[dict]:
    """集保個股查詢頁（qryStock）HTML → 與 openapi 1-5 同構的 records。

    刻意轉成 openapi 的欄名，讓歷史回補與每週 EOD 共用同一支
    `parse_distribution`（級距→散戶/大戶的分類邏輯只有一份真相）。

    表格每列為 `序號 | 級距 | 人數 | 股數 | 占比%`，序號即持股分級 1~17。
    """
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    out: list[dict] = []
    for row in rows:
        cells = [
            re.sub(r"<[^>]+>", "", c).replace("\xa0", " ").strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        ]
        if len(cells) < 5 or not re.fullmatch(r"\d+", cells[0]):
            continue
        out.append(
            {
                _DATE_KEY: data_date.strftime("%Y%m%d"),
                "證券代號": symbol,
                "持股分級": cells[0],
                "人數": cells[2],
                "股數": cells[3],
                "占集保庫存數比例%": cells[4],
            }
        )
    return out
