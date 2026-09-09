"""診斷：四維（含 intraday）與三維標的混在同一橫斷面百分位，是否造成系統性偏差。

背景：composite 在成分缺漏時按 OECD 做法排除該成分並重分配權重，四維與三維的
`composite_raw` 都落在 -1..1，但**分布形狀不同**（成分越多，極端值越容易被稀釋、
往中間集中）。而 chip_score 是把當日全市場的 composite_raw 一起做百分位排名——
若兩種分布混排，有逐筆的標的可能系統性拿不到高分，而「有沒有逐筆」只反映它是不是
Shioaji 抓得到的熱門股，與籌碼好壞無關。

方法：同一天、同一組標的跑兩次 analyze_market——
  A) 原樣（有逐筆者走四維）
  B) 強制全部走三維（把 intraday 欄位就地設 None）
兩者對「有逐筆」那組的分數差異即為**維度效應**；若 A、B 幾乎相同，則該組分數偏低
純粹是標的特性（標的效應），不是結構性偏差，不需修。

用法：
  APP_ENV=dev  python -m scripts.diag_intraday_bias [YYYY-MM-DD]
  APP_ENV=prod python -m scripts.diag_intraday_bias           # 預設最新交易日
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from sqlalchemy import select

from app.db.models.features import FeatureDaily
from app.db.session import get_sessionmaker
from app.repositories.market import load_market_context
from app.services.analysis import AnalysisService, analyze_market

_INTRADAY_COLS = (
    "cvd_z", "large_trade_delta_z", "intraday_obi",
    "absorption_z", "trade_speed_z", "price_efficiency_z",
)


def _summary(results, subset: set[str]) -> dict:
    rows = [r for r in results if r.symbol in subset]
    if not rows:
        return {"n": 0}
    scores = [r.chip.chip_score for r in rows]
    def _m(f):
        return sum(f(r) for r in rows) / len(rows)
    return {
        "n": len(rows),
        "avg": sum(scores) / len(scores),
        "ge75": sum(1 for s in scores if s >= 75),
        "buy": sum(1 for r in rows if r.signal.action.value == "BUY"),
        # 分項均值（各為 0..100，50 中性）+ 合成原始值，用來分辨
        # 「intraday 分項真的偏弱」與「只是分布被稀釋」。
        "raw": _m(lambda r: r.chip.composite_raw),
        "intra": _m(lambda r: r.chip.intraday),
        "inst": _m(lambda r: r.chip.institutional),
        "hold": _m(lambda r: r.chip.holder),
        "mkt": _m(lambda r: r.chip.market),
    }


async def run(target: dt.date | None) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        if target is None:
            target = (
                await s.execute(
                    select(FeatureDaily.data_date)
                    .order_by(FeatureDaily.data_date.desc())
                    .limit(1)
                )
            ).scalar_one()
        rows = list(
            (
                await s.execute(
                    select(FeatureDaily).where(FeatureDaily.data_date == target)
                )
            ).scalars().all()
        )
        market = await load_market_context(s, target)

    with_intra = {r.symbol for r in rows if r.cvd_z is not None}
    print(f"日期 {target}　全市場 {len(rows)} 檔，其中有逐筆 {len(with_intra)} 檔")
    if not with_intra:
        print("該日無逐筆資料 → 無混排問題（本診斷不適用）。")
        return

    svc = AnalysisService()
    a = analyze_market(svc, [(r, None) for r in rows], market)   # 原樣：四維 + 三維混排
    for r in rows:                                               # 就地降成三維
        for c in _INTRADAY_COLS:
            setattr(r, c, None)
    b = analyze_market(svc, [(r, None) for r in rows], market)   # 全部三維

    expected = len(with_intra) * 0.25
    print(f"\n有逐筆的 {len(with_intra)} 檔（若無偏差，≥75 應約 {expected:.0f} 檔）")
    hdr = (f"{'算法':<24}{'平均分':>7}{'≥75':>6}{'佔比':>7}{'BUY':>5}"
           f"{'raw':>8}{'intra':>7}{'inst':>7}{'hold':>7}{'mkt':>7}")
    print(hdr)
    for label, res in (("A 原樣（四維混排）", a), ("B 強制全部三維", b)):
        m = _summary(res, with_intra)
        print(f"{label:<24}{m['avg']:>7.1f}{m['ge75']:>6}{m['ge75'] / m['n']:>7.1%}"
              f"{m['buy']:>5}{m['raw']:>8.3f}{m['intra']:>7.1f}{m['inst']:>7.1f}"
              f"{m['hold']:>7.1f}{m['mkt']:>7.1f}")

    others = {r.symbol for r in rows} - with_intra
    print(f"\n對照：無逐筆的 {len(others)} 檔")
    print(hdr)
    for label, res in (("A 原樣", a), ("B 強制全部三維", b)):
        m = _summary(res, others)
        print(f"{label:<24}{m['avg']:>7.1f}{m['ge75']:>6}{m['ge75'] / m['n']:>7.1%}"
              f"{m['buy']:>5}{m['raw']:>8.3f}{m['intra']:>7.1f}{m['inst']:>7.1f}"
              f"{m['hold']:>7.1f}{m['mkt']:>7.1f}")

    print(
        "\n判讀：A 與 B 在『有逐筆』那組差很多 → 維度效應（結構性偏差，需分組映射）；"
        "\n　　　A ≈ B → 標的效應（該組本來分數就這樣），不需修。"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="診斷四維/三維混排造成的分數偏差")
    p.add_argument("date", nargs="?", help="交易日 YYYY-MM-DD（預設最新）")
    a = p.parse_args()
    asyncio.run(run(dt.date.fromisoformat(a.date) if a.date else None))


if __name__ == "__main__":
    main()
