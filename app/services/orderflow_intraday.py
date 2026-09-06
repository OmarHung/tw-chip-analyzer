"""由當日逐筆計算盤中資金流（CVD、大單、內外盤比）與 intraday 分項分數。

單股即時計算（不需全市場橫斷面）：以「相對當日總量」的有界訊號組合，
故不需 z-score 常態化，直接落在 -1..1 → 轉 0..100。見 docs/03 §8、§10。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Thresholds, get_thresholds
from app.services.normalize import clamp
from app.services.orderflow import cvd as cvd_mod
from app.services.orderflow import large_trade as lt_mod


@dataclass
class OrderFlowResult:
    trade_count: int
    total_volume: int
    buy_volume: int
    sell_volume: int
    buy_ratio: float          # 外盤成交量佔比 buy/(buy+sell)
    cvd_final: float
    cvd_series: list[dict] = field(default_factory=list)  # 每分鐘 {t, cvd}
    large_threshold: float | None = None
    large_buy: float = 0.0
    large_sell: float = 0.0
    large_delta: float = 0.0
    intraday_score: float = 50.0  # 0..100
    net_aggressor: float = 0.0    # -1..1
    large_net: float = 0.0        # -1..1
    cvd_slope: float = 0.0


def compute_orderflow(
    ticks: list[dict], thresholds: Thresholds | None = None
) -> OrderFlowResult:
    """ticks：[{t(epoch秒), price, volume, side, ...}]，依時間升冪。"""
    t = thresholds or get_thresholds()
    lt_cfg = t.large_trade

    if not ticks:
        return OrderFlowResult(0, 0, 0, 0, 0.0, 0.0)

    volumes = [tk["volume"] for tk in ticks]
    total = sum(volumes)
    buy = sum(tk["volume"] for tk in ticks if tk["side"] > 0)
    sell = sum(tk["volume"] for tk in ticks if tk["side"] < 0)

    # CVD（逐筆）+ 每分鐘取樣供繪圖
    deltas = [tk["side"] * tk["volume"] for tk in ticks]
    cvd_running = cvd_mod.cumulative(deltas)
    cvd_final = cvd_running[-1] if cvd_running else 0.0
    series: list[dict] = []
    last_min = None
    for tk, c in zip(ticks, cvd_running):
        m = tk["t"] // 60 * 60
        if m != last_min:
            series.append({"t": m, "cvd": c})
            last_min = m
        else:
            series[-1]["cvd"] = c

    # 大單（rolling quantile，禁固定張數）
    thr = lt_mod.large_threshold(
        volumes, percentile=lt_cfg.get("percentile", 0.95),
        min_samples=lt_cfg.get("min_samples", 500),
    )
    trades = [(tk["side"], tk["volume"]) for tk in ticks]
    large_buy = sum(v for s, v in trades if s > 0 and thr is not None and v >= thr)
    large_sell = sum(v for s, v in trades if s < 0 and thr is not None and v >= thr)
    large_delta = float(large_buy - large_sell)

    # 有界訊號
    net_aggressor = (buy - sell) / total if total else 0.0
    large_total = large_buy + large_sell
    large_net = (large_buy - large_sell) / large_total if large_total else 0.0
    cvd_vals = [p["cvd"] for p in series]
    cvd_slope = cvd_mod.slope(cvd_vals)
    # 斜率正規化：以每分鐘平均量為尺度
    avg_min_vol = total / max(len(series), 1)
    cvd_slope_norm = clamp(cvd_slope / (avg_min_vol + 1e-9))

    # intraday 分項（權重取自 config intraday 中可算部分，重新分配）
    signal = 0.45 * large_net + 0.35 * net_aggressor + 0.20 * cvd_slope_norm
    intraday_score = round(50 + 50 * clamp(signal), 1)

    return OrderFlowResult(
        trade_count=len(ticks),
        total_volume=int(total),
        buy_volume=int(buy),
        sell_volume=int(sell),
        buy_ratio=round(buy / (buy + sell), 4) if (buy + sell) else 0.0,
        cvd_final=float(cvd_final),
        cvd_series=series,
        large_threshold=thr,
        large_buy=float(large_buy),
        large_sell=float(large_sell),
        large_delta=large_delta,
        intraday_score=intraday_score,
        net_aggressor=round(net_aggressor, 4),
        large_net=round(large_net, 4),
        cvd_slope=round(cvd_slope, 2),
    )
