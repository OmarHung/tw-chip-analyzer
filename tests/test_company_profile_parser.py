"""公司基本資料 parser：產業別 / 股數 / 公司網址（熱力圖商標用）。

網址是 MOPS 申報者自填，實際格式混雜（2026-09-15 抽樣 1985 檔）：910 筆無 scheme、
大小寫混用、`https:// www.x.com` 中間夾空白、TPEx 尾端帶全形空白。正規化失敗一律 None，
前端退回代號徽章，不可把垃圾字串當網域送去抓 favicon。
"""
from __future__ import annotations

import pytest

from app.importers.industry import normalize_website, parse_tpex_profiles, parse_twse_profiles


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.tccgroupholdings.com/tw/", "https://www.tccgroupholdings.com/tw/"),
        ("www.acc.com.tw", "https://www.acc.com.tw"),
        ("WWW.FOPCO.COM.TW", "https://www.fopco.com.tw"),
        ("https:// www.acergaming.com", "https://www.acergaming.com"),
        ("https://www.morn-sun.com.tw/　", "https://www.morn-sun.com.tw/"),
        ("http://www.wfe.com.tw", "http://www.wfe.com.tw"),
    ],
)
def test_normalize_website_accepts_messy_but_valid_urls(raw, expected):
    assert normalize_website(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "－", "無", "localhost", "ftp://x.com.tw", "http://"])
def test_normalize_website_rejects_non_urls(raw):
    assert normalize_website(raw) is None


def test_parse_twse_profiles_includes_website():
    rows = parse_twse_profiles([
        {"公司代號": "1101", "產業別": "01", "已發行普通股數或TDR原股發行股數": "7523181742",
         "網址": "https://www.tccgroupholdings.com/tw/"},
    ])
    assert rows == [{
        "symbol": "1101", "industry": "水泥", "shares_outstanding": 7523181742,
        "website": "https://www.tccgroupholdings.com/tw/",
    }]


def test_parse_tpex_profiles_includes_website():
    rows = parse_tpex_profiles([
        {"SecuritiesCompanyCode": "1240", "SecuritiesIndustryCode": "33",
         "IssueShares": "44232373", "WebAddress": "https://www.morn-sun.com.tw/　"},
    ])
    assert rows[0]["website"] == "https://www.morn-sun.com.tw/"
