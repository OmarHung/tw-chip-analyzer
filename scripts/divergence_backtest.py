"""背離訊號 forward-return 研究：量價背離狀態是否對未來報酬有單調 alpha？

用法：
    APP_ENV=dev python -m scripts.divergence_backtest [--window 20] [--step 5] \
        [--min-turnover 20000000] [--max-symbols N]

方法（look-ahead 安全）：
- 訊號日 D:用 data_date<=D 的收盤/成交量/三大法人淨買超算 compute_divergence 狀態。
  DB 的 available_at = 當日 15:00(收盤後),故 data_date<=D 等價於「D 收盤時已知」。
- 進場:BacktestEngine 取 data_date 之後第一根 bar 的 open(嚴格 >D,無 look-ahead)。
- forward return 用 net(含手續費/稅/滑價,禁 0 成本)。
- 依背離狀態分桶,列各 horizon 的 n / 勝率 / 平均 / 中位數,檢查單調性。

鐵則 9:這是「聲稱背離有效」的必要前提;無此證據前不得宣稱可用。
"""
from __future__ import annotations

import argparse
import asyncio
import statistics as st
from collections import defaultdict

from sqlalchemy import select

from app.backtest.engine import BacktestEngine, BacktestSignal
from app.backtest.forward_returns import Bar
from app.core.config import get_thresholds
from app.db.models.chips import InstitutionalDaily
from app.db.models.market import DailyPrice
from app.db.session import get_sessionmaker
from app.services.flows import compute_divergence

# 期望排序（若訊號有效，forward return 應由低到高遞增）
STATUS_ORDER = [
    "bearish_div",
    "aligned_down",
    "neutral",
    "aligned_up",
    "bullish_div",
]
STATUS_ZH = {
    "bearish_div": "負背離",
    "aligned_down": "同向偏空",
    "neutral": "中性",
    "aligned_up": "同向偏多",
    "bullish_div": "正背離",
}


def _net_lots(r: InstitutionalDaily) -> float:
    parts = [r.foreign_net, r.trust_net, r.dealer_self_net, r.dealer_hedge_net]
    return sum(p for p in parts if p is not None) / 1000.0


async def _load() -> tuple[dict[str, list[Bar]], dict[str, dict], dict[str, dict], dict[str, float]]:
    """回傳 (每股 bars 升冪, 每股 {date:三大法人淨買超張}, 每股 {date:成交張數}, 每股中位日成交額)。"""
    sm = get_sessionmaker()
    bars: dict[str, list[Bar]] = defaultdict(list)
    inst: dict[str, dict] = defaultdict(dict)
    vol: dict[str, dict] = defaultdict(dict)
    turnovers: dict[str, list[float]] = defaultdict(list)
    async with sm() as s:
        prows = (
            await s.execute(select(DailyPrice).order_by(DailyPrice.symbol, DailyPrice.data_date))
        ).scalars()
        for r in prows:
            if None in (r.open, r.high, r.low, r.close):
                continue
            bars[r.symbol].append(
                Bar(r.data_date, float(r.open), float(r.high), float(r.low), float(r.close))
            )
            if r.volume:
                vol[r.symbol][r.data_date] = r.volume / 1000.0
            if r.turnover is not None:
                turnovers[r.symbol].append(float(r.turnover))
        irows = (
            await s.execute(
                select(InstitutionalDaily).order_by(
                    InstitutionalDaily.symbol, InstitutionalDaily.data_date
                )
            )
        ).scalars()
        for r in irows:
            inst[r.symbol][r.data_date] = _net_lots(r)
    med_turnover = {sym: (st.median(v) if v else 0.0) for sym, v in turnovers.items()}
    return bars, inst, vol, med_turnover


def run_study(
    bars_by_sym: dict[str, list[Bar]],
    inst_by_sym: dict[str, dict],
    vol_by_sym: dict[str, dict],
    med_turnover: dict[str, float],
    *,
    window: int,
    step: int,
    min_turnover: float,
    max_symbols: int | None,
) -> dict:
    th = get_thresholds()
    dcfg = th.divergence
    price_eps = float(dcfg.get("price_eps", 0.03))
    flow_eps = float(dcfg.get("flow_eps", 0.02))
    min_points = int(dcfg.get("min_points", 10))
    engine = BacktestEngine()
    horizons = engine.horizons
    max_h = max(horizons)

    rets: dict[tuple[str, int], list[float]] = defaultdict(list)
    n_signals = 0

    syms = sorted(bars_by_sym)
    if max_symbols:
        syms = syms[:max_symbols]
    for sym in syms:
        if med_turnover.get(sym, 0.0) < min_turnover:
            continue
        bars = bars_by_sym[sym]
        n = len(bars)
        if n < window + max_h + 1:
            continue
        idate = inst_by_sym.get(sym, {})
        vdate = vol_by_sym.get(sym, {})
        closes = [b.close for b in bars]
        inst_arr: list[float | None] = [idate.get(b.date) for b in bars]
        vols: list[float | None] = [vdate.get(b.date) for b in bars]

        for i in range(window - 1, n - max_h - 1, step):
            div = compute_divergence(
                closes[: i + 1],
                inst_arr[: i + 1],
                vols[: i + 1],
                window=window,
                price_eps=price_eps,
                flow_eps=flow_eps,
                min_points=min_points,
            )
            if div is None:
                continue
            sig = BacktestSignal(sym, bars[i].date, 0.0)
            oc = engine.evaluate_signal(sig, bars)
            if oc is None:
                continue
            n_signals += 1
            for h, hr in oc.forward.horizons.items():
                rets[(div.status, h)].append(hr.net_return)

    return {"horizons": horizons, "rets": rets, "n_signals": n_signals}


def _fmt(res: dict, window: int) -> str:
    horizons = res["horizons"]
    rets = res["rets"]
    lines = [
        f"\n=== 背離訊號 × forward return（window={window}, 淨報酬）===",
        f"訊號總數 n={res['n_signals']}",
    ]
    for h in horizons:
        lines.append(f"\n--- horizon {h}D ---")
        lines.append(f"{'狀態':<10}{'n':>7}{'win%':>8}{'avg':>9}{'median':>9}")
        means: list[tuple[str, float]] = []
        for s in STATUS_ORDER:
            v = rets.get((s, h), [])
            if not v:
                lines.append(f"{STATUS_ZH[s]:<10}{0:>7}{'-':>8}")
                continue
            win = sum(1 for x in v if x > 0) / len(v)
            m = st.mean(v)
            means.append((s, m))
            lines.append(
                f"{STATUS_ZH[s]:<10}{len(v):>7}{win:>7.0%}{m:>9.2%}{st.median(v):>9.2%}"
            )
        # 單調性：正背離 avg 應 > 負背離 avg
        d = dict(means)
        if "bullish_div" in d and "bearish_div" in d:
            spread = d["bullish_div"] - d["bearish_div"]
            lines.append(f"  正背離−負背離 價差 = {spread:+.2%}")
    return "\n".join(lines)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=20)
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--min-turnover", type=float, default=20_000_000)
    ap.add_argument("--max-symbols", type=int, default=None)
    args = ap.parse_args()

    bars, inst, vol, med = await _load()
    res = run_study(
        bars,
        inst,
        vol,
        med,
        window=args.window,
        step=args.step,
        min_turnover=args.min_turnover,
        max_symbols=args.max_symbols,
    )
    print(_fmt(res, args.window))


if __name__ == "__main__":
    asyncio.run(main())
