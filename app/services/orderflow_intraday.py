"""由當日逐筆計算盤中資金流（CVD、大單、內外盤比）與 intraday 分項分數。

單股即時計算（不需全市場橫斷面）：以「相對當日總量」的有界訊號組合，
故不需 z-score 常態化，直接落在 -1..1 → 轉 0..100。見 docs/03 §8、§10。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Thresholds, get_thresholds
from app.services.normalize import clamp, zscore
from app.services.orderflow import absorption as abs_mod
from app.services.orderflow import cvd as cvd_mod
from app.services.orderflow import large_trade as lt_mod
from app.services.orderflow.price_efficiency import price_efficiency as _price_efficiency


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
    cvd_slope_norm: float = 0.0   # -1..1（以每分鐘平均量正規化，turnover-neutral）
    absorption_signal: float = 0.0   # -1..1（sell absorption 偏多為正）
    trade_speed_signal: float = 0.0  # -1..1（盤中後段成交加速為正）
    price_efficiency: float = 0.0    # -1..1（單向上行為正）


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
    bars: list[dict] = []  # 每分鐘 K：price(收)/volume(和)/count/delta(和)，供三成分
    last_min = None
    for tk, c, d in zip(ticks, cvd_running, deltas):
        m = tk["t"] // 60 * 60
        if m != last_min:
            series.append({"t": m, "cvd": c})
            bars.append({"price": tk["price"], "volume": tk["volume"],
                         "count": 1, "delta": d})
            last_min = m
        else:
            series[-1]["cvd"] = c
            b = bars[-1]
            b["price"] = tk["price"]
            b["volume"] += tk["volume"]
            b["count"] += 1
            b["delta"] += d

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

    # intraday 三成分（價格結構 / 吸收 / 速度；皆 -1..1、單股自身序列）
    price_eff = _price_efficiency([b["price"] for b in bars])
    absorption_signal = _bar_absorption(bars)
    trade_speed_signal = _bar_trade_speed(bars)

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
        cvd_slope_norm=round(cvd_slope_norm, 4),
        absorption_signal=round(absorption_signal, 4),
        trade_speed_signal=round(trade_speed_signal, 4),
        price_efficiency=round(price_eff, 4),
    )


def _mean_std(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    m = sum(xs) / n
    return m, (sum((x - m) ** 2 for x in xs) / n) ** 0.5


def _bar_absorption(bars: list[dict]) -> float:
    """逐分鐘 directional_absorption 聚合為 -1..1（sell absorption 偏多為正）。

    只計「量高於當日均量」的分鐘（vz>0）才算吸收，故 buy/sell_abs 皆 ≥0、比率穩定。
    """
    if len(bars) < 3:
        return 0.0
    vols = [float(b["volume"]) for b in bars]
    rets = [0.0]
    for i in range(1, len(bars)):
        prev = bars[i - 1]["price"]
        rets.append((bars[i]["price"] - prev) / prev if prev else 0.0)
    vmean, vstd = _mean_std(vols)
    rmean, rstd = _mean_std(rets)
    buy_abs = sell_abs = 0.0
    for i, b in enumerate(bars):
        vz = zscore(vols[i], vmean, vstd)
        if vz <= 0:
            continue  # 低於均量的分鐘無吸收意義
        rz = zscore(rets[i], rmean, rstd)
        side = 1 if b["delta"] > 0 else -1 if b["delta"] < 0 else 0
        ba, sa = abs_mod.directional_absorption(vz, rz, side)
        buy_abs += ba
        sell_abs += sa
    tot = buy_abs + sell_abs
    return clamp((sell_abs - buy_abs) / tot) if tot > 0 else 0.0


def _bar_trade_speed(bars: list[dict]) -> float:
    """盤中後半 vs 前半成交筆數速度變化 → -1..1（後段加速為正）。"""
    if len(bars) < 4:
        return 0.0
    mid = len(bars) // 2
    early = sum(b["count"] for b in bars[:mid])
    late = sum(b["count"] for b in bars[mid:])
    tot = early + late
    return clamp((late - early) / tot) if tot > 0 else 0.0
