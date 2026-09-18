"""假設檢驗:「底部吸籌」——位在價格區間底部的股票,Chip Score 高者日後是否漲得比較好?

事先寫定(跑之前定義,跑完不調):
- 價格位置 pos = (close - 區間最低) / (區間最高 - 區間最低),區間 = 訊號日(含)往前 N 根
  後復權收盤(預設 N=40)。只用 data_date 當日及以前的 bar,無 look-ahead。
- 底部 = pos ≤ 0.3;頂部 = pos ≥ 0.7(對照組)。
- 高分 = chip_score ≥ 75,低分 = chip_score < 25(百分位映射下約為前/後 25%)。
- 報酬沿用 BacktestEngine:市場次一交易日開盤進場、含交易成本,持有 10/20/40/60 日。
- 每日算「高分平均 − 低分平均」與組內 Spearman IC,再對逐日序列取平均與
  Newey-West t(lag = horizon − 1,修正重疊報酬的自相關)。

紀律:此腳本只用於記錄假設是否有跡象,**不據此調整權重**(見 CLAUDE.md §28 相關段落)。
樣本僅約 130 個交易日;長 horizon 的「獨立區塊數」≈ 有效天數 / horizon,會一併印出。

用法:
    APP_ENV=dev python -m scripts.bottom_accumulation_check [--window 40]
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

import numpy as np
import pandas as pd

from app.backtest import BacktestEngine
from app.backtest.metrics import newey_west_t
from app.backtest.runner import load_bars, load_signals, market_calendar
from app.db.session import get_sessionmaker

HORIZONS = [10, 20, 40, 60]
BOTTOM_MAX = 0.3
TOP_MIN = 0.7
HIGH_SCORE = 75.0
LOW_SCORE = 25.0
MIN_PER_GROUP = 5  # 每日每組至少幾檔才計入該日


def _price_position(bars, window: int) -> dict:
    """(data_date) -> 區間位置 pos;歷史不足 window 根者不給。"""
    closes = np.array([b.close for b in bars], dtype=float)
    out = {}
    for i in range(window - 1, len(bars)):
        seg = closes[i - window + 1 : i + 1]
        lo, hi = seg.min(), seg.max()
        if hi > lo:
            out[bars[i].date] = (closes[i] - lo) / (hi - lo)
    return out


def _spearman(x: pd.Series, y: pd.Series) -> float | None:
    if len(x) < 10:
        return None
    return float(x.rank().corr(y.rank()))


def _summarize(df: pd.DataFrame, h: int, label: str) -> None:
    """df 欄位:date, score, ret(已限定在某價格位置子集)。"""
    spreads, ics = [], []
    n_hi = n_lo = 0
    for _, g in df.groupby("date"):
        hi = g.loc[g.score >= HIGH_SCORE, "ret"]
        lo = g.loc[g.score < LOW_SCORE, "ret"]
        if len(hi) >= MIN_PER_GROUP and len(lo) >= MIN_PER_GROUP:
            spreads.append(hi.mean() - lo.mean())
            n_hi += len(hi)
            n_lo += len(lo)
        ic = _spearman(g.score, g.ret)
        if ic is not None:
            ics.append(ic)
    days = len(spreads)
    if days < 2:
        print(f"  {label:<6} 有效日不足({days})")
        return
    sp_t = newey_west_t(spreads, lags=h - 1)
    ic_t = newey_west_t(ics, lags=h - 1) if len(ics) >= 2 else None
    print(
        f"  {label:<6} 日={days:>3} 獨立區塊≈{days / h:4.1f} "
        f"高分n={n_hi:>6} 低分n={n_lo:>6} | "
        f"高−低 {np.mean(spreads) * 100:+6.2f}% (NW t {sp_t:+5.2f}) | "
        f"IC {np.mean(ics):+.4f} (NW t {ic_t:+5.2f})"
    )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=40, help="價格區間回看根數")
    args = ap.parse_args()

    sm = get_sessionmaker()
    async with sm() as s:
        signals = await load_signals(s)
        prices = await load_bars(s, sorted({sig.symbol for sig in signals}))
    calendar = market_calendar(prices)

    positions = {sym: _price_position(bars, args.window) for sym, bars in prices.items()}

    engine = BacktestEngine()
    engine.horizons = HORIZONS
    rows: dict[int, list[tuple]] = defaultdict(list)
    for sig in signals:
        pos = positions.get(sig.symbol, {}).get(sig.data_date)
        bars = prices.get(sig.symbol)
        if pos is None or not bars:
            continue
        oc = engine.evaluate_signal(sig, bars, calendar)
        if oc is None:
            continue
        for h in HORIZONS:
            hr = oc.forward.horizons.get(h)
            if hr is not None:
                rows[h].append((sig.data_date, sig.chip_score, pos, hr.net_return))

    print(
        f"價格區間={args.window} 根  底部 pos≤{BOTTOM_MAX}  頂部 pos≥{TOP_MIN}  "
        f"高分≥{HIGH_SCORE:.0f} 低分<{LOW_SCORE:.0f}"
    )
    for h in HORIZONS:
        df = pd.DataFrame(rows[h], columns=["date", "score", "pos", "ret"])
        print(f"\n[{h}D]")
        if df.empty:
            print("  無樣本")
            continue
        _summarize(df, h, "全部")
        _summarize(df[df.pos <= BOTTOM_MAX], h, "底部")
        _summarize(df[df.pos >= TOP_MIN], h, "頂部")


if __name__ == "__main__":
    asyncio.run(main())
