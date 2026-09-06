"""主力進出 vs 股價的量價背離偵測（純函式，供 flows API 與測試使用）。

背離語意（台股）:
- 正背離(bullish_div):股價區間下跌,但三大法人淨買超為正 → 主力逢低吸籌。
- 負背離(bearish_div):股價區間上漲,但三大法人淨賣超 → 主力逢高出貨。
- 量價同向(aligned_up / aligned_down):價與主力同方向。
- 中性(neutral):任一方幅度不足門檻。

正規化(鐵則 6):主力淨買超以「佔區間成交比重」表示(dimensionless),不直接比張數,跨股票可比。
所有門檻由 config/thresholds.yaml 的 `divergence` 段提供(鐵則 4/5)。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DivergenceResult:
    window: int
    price_return: float | None  # 區間報酬(小數,0.05=+5%)
    inst_net: float  # 區間三大法人淨買超合計(張,買超為正)
    inst_flow_ratio: float | None  # 淨買超佔區間成交比重(小數)
    price_inst_corr: float | None  # 日價格變動 vs 日主力淨買超 之相關係數
    status: str  # bullish_div / bearish_div / aligned_up / aligned_down / neutral
    label: str  # 中文標籤
    note: str  # 一句解讀


_LABELS = {
    "bullish_div": "正背離",
    "bearish_div": "負背離",
    "aligned_up": "量價同向偏多",
    "aligned_down": "量價同向偏空",
    "neutral": "中性",
}

_NOTES = {
    "bullish_div": "股價走弱但主力持續買超，疑似逢低吸籌。",
    "bearish_div": "股價走強但主力轉為賣超，疑似逢高出貨。",
    "aligned_up": "股價與主力同步走揚，量價配合。",
    "aligned_down": "股價與主力同步走弱，賣壓一致。",
    "neutral": "價格或主力任一方幅度不足，無明顯背離。",
}


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """手算 Pearson 相關;樣本 <2 或任一方零變異則回 None。"""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / (sxx**0.5 * syy**0.5)


@dataclass
class CostBasisResult:
    costs: list[float | None]  # 對齊輸入的每日估算主力成本(元;未建倉為 None)
    latest_cost: float | None
    latest_price: float | None
    premium_pct: float | None  # 現價/成本 - 1(正=主力浮盈,負=套牢)
    state: str  # profit / loss / flat / unknown
    label: str


_COST_LABELS = {
    "profit": "主力浮盈",
    "loss": "主力套牢",
    "flat": "接近打平",
    "unknown": "無法估算",
}


def compute_cost_basis(
    prices: list[float | None],
    inst_nets: list[float | None],
    *,
    state_eps: float,
) -> CostBasisResult:
    """移動加權平均成本法估算主力持倉均價。

    prices:每日成交均價(元,建議用 VWAP=成交金額/成交量);inst_nets:當日三大法人
    淨買超(張,買超為正)。淨買日以當日價加權更新均價;淨賣日減倉、均價不變,減至 0
    重置。持倉自窗首 0 起算,故僅反映窗內觀察到的累積(標示為估算)。
    """
    if len(prices) != len(inst_nets):
        raise ValueError("prices / inst_nets 長度必須一致")

    pos = 0.0
    cost: float | None = None
    costs: list[float | None] = []
    for p, net in zip(prices, inst_nets):
        if p is not None and p > 0 and net is not None:
            if net > 0:
                new_pos = pos + net
                cost = p if (cost is None or pos <= 0) else (pos * cost + net * p) / new_pos
                pos = new_pos
            elif net < 0:
                pos += net
                if pos <= 0:
                    pos = 0.0
                    cost = None
        costs.append(cost)

    latest_price = next((p for p in reversed(prices) if p is not None and p > 0), None)
    latest_cost = cost
    premium_pct: float | None = None
    state = "unknown"
    if latest_cost is not None and latest_cost > 0 and latest_price is not None:
        premium_pct = latest_price / latest_cost - 1.0
        if premium_pct > state_eps:
            state = "profit"
        elif premium_pct < -state_eps:
            state = "loss"
        else:
            state = "flat"

    return CostBasisResult(
        costs=costs,
        latest_cost=latest_cost,
        latest_price=latest_price,
        premium_pct=premium_pct,
        state=state,
        label=_COST_LABELS[state],
    )


def compute_divergence(
    closes: list[float | None],
    inst_nets: list[float | None],
    volumes_lots: list[float | None],
    *,
    window: int,
    price_eps: float,
    flow_eps: float,
    min_points: int,
) -> DivergenceResult | None:
    """對齊的日序列(升冪,尾端為最新)計算單一窗口的背離。

    closes:收盤價;inst_nets:當日三大法人合計淨買超(張);volumes_lots:當日成交量(張)。
    三者等長且同索引對應同一交易日。資料不足或缺漏過多回 None。
    """
    if not (len(closes) == len(inst_nets) == len(volumes_lots)):
        raise ValueError("closes / inst_nets / volumes_lots 長度必須一致")
    if window < 2:
        return None

    c = closes[-window:]
    inet = inst_nets[-window:]
    vol = volumes_lots[-window:]

    # 區間報酬:取窗內第一個與最後一個有效收盤
    valid_close = [(i, x) for i, x in enumerate(c) if x is not None and x > 0]
    price_return: float | None = None
    if len(valid_close) >= 2:
        c0 = valid_close[0][1]
        c1 = valid_close[-1][1]
        price_return = c1 / c0 - 1.0

    # 主力淨買超與佔成交比重
    inst_net = sum(x for x in inet if x is not None)
    vol_sum = sum(v for v in vol if v is not None)
    inst_flow_ratio: float | None = inst_net / vol_sum if vol_sum > 0 else None

    # 日價格變動 vs 日主力淨買超 相關(需成對有效)
    xs: list[float] = []
    ys: list[float] = []
    prev: float | None = None
    for cl, net in zip(c, inet):
        if cl is not None and cl > 0 and prev is not None and prev > 0 and net is not None:
            xs.append(cl / prev - 1.0)
            ys.append(net)
        if cl is not None and cl > 0:
            prev = cl
    corr = _pearson(xs, ys)

    valid_points = min(len(valid_close), sum(1 for x in inet if x is not None))
    if valid_points < min_points or price_return is None or inst_flow_ratio is None:
        status = "neutral"
    else:
        price_up = price_return >= price_eps
        price_dn = price_return <= -price_eps
        flow_up = inst_flow_ratio >= flow_eps
        flow_dn = inst_flow_ratio <= -flow_eps
        if price_dn and flow_up:
            status = "bullish_div"
        elif price_up and flow_dn:
            status = "bearish_div"
        elif price_up and flow_up:
            status = "aligned_up"
        elif price_dn and flow_dn:
            status = "aligned_down"
        else:
            status = "neutral"

    return DivergenceResult(
        window=window,
        price_return=price_return,
        inst_net=inst_net,
        inst_flow_ratio=inst_flow_ratio,
        price_inst_corr=corr,
        status=status,
        label=_LABELS[status],
        note=_NOTES[status],
    )
