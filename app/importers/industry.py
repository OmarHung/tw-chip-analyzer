"""公司基本資料 → 產業別 / 已發行股數（MOPS openapi，上市與上櫃同一套代碼）。

TWSE `t187ap03_L` 用中文欄名、TPEx `mopsfin_t187ap03_O` 用英文欄名，內容同構。
產業別只給代碼，這裡轉成中文名稱後存入 `stock.industry`——分組以名稱為準，上市/
上櫃同產業才會落在同一組（industry_trend 需要跨市場一致的分組）。
"""
from __future__ import annotations

from typing import Any

from app.importers.base import is_stock_symbol

# MOPS 產業別代碼（公開發行公司基本資料）。抽樣核對過：
# 1101 台泥=01、2330 台積電=24、2317 鴻海=31、2603 長榮=15、2882 國泰金=17、
# 3008 大立光=26、2412 中華電=27、9910 豐泰=37。
INDUSTRY_NAMES: dict[str, str] = {
    "01": "水泥",
    "02": "食品",
    "03": "塑膠",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "08": "玻璃陶瓷",
    "09": "造紙",
    "10": "鋼鐵",
    "11": "橡膠",
    "12": "汽車",
    "14": "建材營造",
    "15": "航運",
    "16": "觀光餐旅",
    "17": "金融保險",
    "18": "貿易百貨",
    "19": "綜合",
    "20": "其他",
    "21": "化學工業",
    "22": "生技醫療",
    "23": "油電燃氣",
    "24": "半導體",
    "25": "電腦及週邊",
    "26": "光電",
    "27": "通信網路",
    "28": "電子零組件",
    "29": "電子通路",
    "30": "資訊服務",
    "31": "其他電子",
    "32": "文化創意",
    "33": "農業科技",
    "34": "電子商務",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
    "80": "管理股票",
}


def _shares(v: Any) -> int | None:
    """已發行普通股數（字串含逗號；空/非數字回 None）。"""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if not s or not s.lstrip("-").isdigit():
        return None
    n = int(s)
    return n if n > 0 else None


def _row(sym: str, code: Any, shares: Any) -> dict | None:
    sym = str(sym).strip()
    if not is_stock_symbol(sym):
        return None
    c = str(code).strip().zfill(2) if code not in (None, "") else ""
    # 代碼不在對照表時保留原碼（分組仍正確，只是顯示不好看）
    return {
        "symbol": sym,
        "industry": INDUSTRY_NAMES.get(c, c or None),
        "shares_outstanding": _shares(shares),
    }


def parse_twse_profiles(raw: list[dict]) -> list[dict]:
    """上市公司基本資料（中文欄名）。"""
    out = [
        r
        for x in raw or []
        if (r := _row(x.get("公司代號"), x.get("產業別"), x.get("已發行普通股數或TDR原股發行股數")))
    ]
    return out


def parse_tpex_profiles(raw: list[dict]) -> list[dict]:
    """上櫃公司基本資料（英文欄名）。"""
    out = [
        r
        for x in raw or []
        if (r := _row(
            x.get("SecuritiesCompanyCode"),
            x.get("SecuritiesIndustryCode"),
            x.get("IssueShares"),
        ))
    ]
    return out
