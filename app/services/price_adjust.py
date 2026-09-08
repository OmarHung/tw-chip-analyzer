"""還原價：用除權除息事件把原始價格序列調成連續（back-adjust）。

除權息／拆股當天，原始收盤序列出現非交易性斷點（配息/配股不是真跌），會污染
報酬/MA/ATR。慣例採「後復權（back-adjust）」：**最新一根維持原始價不動**，較早的
價格乘上其後所有除權息因子之累積，使跨除權息的報酬/MA/ATR 連續。

`CorporateAction.adj_factor = 除權息參考價 / 除權息前收盤價`（配息 <1）。
bar 日期 d 的累積因子 = ∏ factor(ex_date > d)：除權息日「之前」的價才乘（<d 的 bar
屬權前價），除權息日當天與之後的 bar 已是權後價、不調整。

同一組函式也用來還原**成交量**：把 factor 換成 `CorporateAction.share_factor`
（1 舊股→幾新股）即可，把歷史量換算成現在的股數單位。價因子 ≠ 量因子——除權息混了
不改股數的現金股利，不可拿 adj_factor 調量。

純函式、無 DB/IO，供 feature_builder 與 backtest 共用。
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence


def cumulative_factors(
    dates: Sequence[dt.date], actions: Sequence[tuple[dt.date, float]]
) -> list[float]:
    """回傳與 dates 對齊的累積還原因子。

    dates：升冪排序的交易日；actions：[(ex_date, adj_factor)]（factor 為 None/<=0 略過）。
    bar 日期 d 的因子 = ∏ factor(ex_date > d)。無事件時全 1.0。
    """
    valid = [(ex, float(f)) for ex, f in actions if f and float(f) > 0]
    if not valid:
        return [1.0] * len(dates)
    out: list[float] = []
    for d in dates:
        f = 1.0
        for ex, fac in valid:
            if ex > d:
                f *= fac
        out.append(f)
    return out


def back_adjust(
    dates: Sequence[dt.date],
    series: Sequence[float],
    actions: Sequence[tuple[dt.date, float]],
) -> list[float]:
    """把單一價格序列（close/high/low…）後復權。回傳與輸入等長的還原價 list。"""
    facs = cumulative_factors(dates, actions)
    return [
        (float(v) * f if v is not None else v)
        for v, f in zip(series, facs)
    ]
