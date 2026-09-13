"""Backtest 引擎（見 docs/06、docs/08 §31）。

流程：訊號 → look-ahead 安全進場（data_date 之後第一根 bar）→ forward return/MFE/MAE
→ 依 score bucket 與 threshold 聚合績效。核心目的：驗證「score 越高、報酬越好」。
"""
from __future__ import annotations

import bisect
import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.backtest.costs import CostModel
from app.backtest.forward_returns import Bar, ForwardResult, compute_forward
from app.backtest.metrics import ReturnStats, summarize
from app.core.config import Thresholds, get_thresholds


@dataclass
class BacktestSignal:
    symbol: str
    data_date: dt.date
    chip_score: float


@dataclass
class SignalOutcome:
    signal: BacktestSignal
    forward: ForwardResult
    success: bool | None  # 依 success config 判定（資料不足為 None）


@dataclass
class BucketReport:
    label: str
    count: int
    by_horizon: dict[int, ReturnStats] = field(default_factory=dict)


@dataclass
class BacktestReport:
    total_signals: int
    evaluated: int
    dropped: int  # 完全無後續 bar 可評估
    horizons: list[int]
    by_bucket: list[BucketReport]
    by_threshold: list[BucketReport]
    success_rate: float | None


def market_calendar(prices: Mapping[str, Sequence[Bar]]) -> list[dt.date]:
    """所有標的 bar 日期的聯集（升冪）＝市場交易日曆。"""
    return sorted({b.date for bars in prices.values() for b in bars})


class BacktestEngine:
    def __init__(
        self,
        thresholds: Thresholds | None = None,
        costs: CostModel | None = None,
        calendar: Sequence[dt.date] | None = None,
    ):
        self.t = thresholds or get_thresholds()
        self.bt = self.t.backtest
        self.costs = costs or CostModel.from_config()
        self.horizons: list[int] = list(self.bt.get("horizons", [1, 3, 5, 10, 20]))
        self.entry_price_field: str = self.bt.get("entry_price", "open")
        # 市場交易日曆（升冪）。有值時進場 bar 必須恰為訊號日的「市場」次一交易日——
        # 停牌數週後的復牌日不是策略會執行的進場（docs/09 BUG-15）。
        self.calendar: list[dt.date] | None = sorted(calendar) if calendar else None

    # --- 單一訊號 ---
    def _entry_index(
        self,
        bars: Sequence[Bar],
        data_date: dt.date,
        calendar: Sequence[dt.date] | None = None,
    ) -> int | None:
        """data_date 之後第一根 bar（嚴格大於，確保無 look-ahead）。

        給了 calendar 時，該 bar 必須落在市場次一交易日；否則（當日停牌/無成交）丟棄。
        """
        expected: dt.date | None = None
        if calendar:
            pos = bisect.bisect_right(calendar, data_date)
            if pos >= len(calendar):
                return None
            expected = calendar[pos]
        for i, b in enumerate(bars):
            if b.date > data_date:
                return i if expected is None or b.date == expected else None
        return None

    def evaluate_signal(
        self,
        sig: BacktestSignal,
        bars: Sequence[Bar],
        calendar: Sequence[dt.date] | None = None,
    ) -> SignalOutcome | None:
        idx = self._entry_index(bars, sig.data_date, calendar or self.calendar)
        if idx is None:
            return None
        future = bars[idx:]
        entry_bar = future[0]
        entry_price = getattr(entry_bar, self.entry_price_field)
        if entry_price is None or entry_price <= 0:
            return None
        fwd = compute_forward(
            entry_price, future, self.horizons, self.costs, entry_bar.date
        )
        return SignalOutcome(sig, fwd, self._success(fwd))

    def _success(self, fwd: ForwardResult) -> bool | None:
        cfg = self.bt.get("success", {})
        h = cfg.get("horizon", 5)
        hr = fwd.horizons.get(h)
        if hr is None:
            return None
        return hr.mfe >= cfg.get("min_mfe", 0.06) and hr.mae >= -cfg.get(
            "max_mae", 0.04
        )

    # --- 批次 + 聚合 ---
    def run(
        self, signals: Sequence[BacktestSignal], prices: Mapping[str, Sequence[Bar]]
    ) -> BacktestReport:
        outcomes: list[SignalOutcome] = []
        dropped = 0
        # 未指定日曆時以本批全部標的的 bar 日期為市場交易日
        calendar = self.calendar or market_calendar(prices)
        for sig in signals:
            bars = prices.get(sig.symbol)
            if not bars:
                dropped += 1
                continue
            oc = self.evaluate_signal(sig, list(bars), calendar)
            if oc is None:
                dropped += 1
                continue
            outcomes.append(oc)

        by_bucket = self._aggregate(
            outcomes,
            [
                (f"[{lo},{hi})", lambda s, lo=lo, hi=hi: lo <= s.chip_score < hi)
                for lo, hi in self.bt.get("score_buckets", [])
            ],
        )
        by_threshold = self._aggregate(
            outcomes,
            [
                (f">={thr}", lambda s, thr=thr: s.chip_score >= thr)
                for thr in self.bt.get("score_thresholds", [])
            ],
        )

        judged = [o.success for o in outcomes if o.success is not None]
        success_rate = (sum(judged) / len(judged)) if judged else None

        return BacktestReport(
            total_signals=len(signals),
            evaluated=len(outcomes),
            dropped=dropped,
            horizons=self.horizons,
            by_bucket=by_bucket,
            by_threshold=by_threshold,
            success_rate=success_rate,
        )

    def _aggregate(self, outcomes, groups) -> list[BucketReport]:
        reports: list[BucketReport] = []
        for label, pred in groups:
            members = [o for o in outcomes if pred(o.signal)]
            br = BucketReport(label=label, count=len(members))
            for h in self.horizons:
                rets = [
                    o.forward.horizons[h].net_return
                    for o in members
                    if h in o.forward.horizons
                ]
                mfes = [
                    o.forward.horizons[h].mfe
                    for o in members
                    if h in o.forward.horizons
                ]
                maes = [
                    o.forward.horizons[h].mae
                    for o in members
                    if h in o.forward.horizons
                ]
                br.by_horizon[h] = summarize(rets, mfes, maes)
            reports.append(br)
        return reports


def format_bucket_table(report: BacktestReport, horizon: int = 5) -> str:
    """輸出 score bucket × 指定 horizon 的績效表（驗證單調性）。"""
    lines = [
        f"訊號數={report.total_signals} 已評估={report.evaluated} 略過={report.dropped}"
        + (
            f" 成功率={report.success_rate:.1%}"
            if report.success_rate is not None
            else ""
        ),
        f"\n=== Score Bucket × {horizon}D（net）===",
        f"{'bucket':<10}{'n':>5}{'win%':>8}{'avg':>9}{'median':>9}{'PF':>7}{'avgMAE':>9}",
    ]
    for br in report.by_bucket:
        s = br.by_horizon.get(horizon)
        if not s or s.count == 0:
            lines.append(f"{br.label:<10}{br.count:>5}{'-':>8}")
            continue
        pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
        lines.append(
            f"{br.label:<10}{s.count:>5}{s.win_rate:>7.0%}{s.avg_return:>9.2%}"
            f"{s.median_return:>9.2%}{pf:>7}{s.avg_mae:>9.2%}"
        )
    return "\n".join(lines)
