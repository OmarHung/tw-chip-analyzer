# 03 · Order Flow 演算法與 Score 設計

> 對應原始交接文件 §8–11

## 8. Order Flow 演算法

### Aggressor Side

```python
if trade_price >= ask1:
    side = BUY
elif trade_price <= bid1:
    side = SELL
else:
    # fallback tick rule
    if price > previous_price:
        side = BUY
    elif price < previous_price:
        side = SELL
    else:
        side = UNKNOWN
```

**禁止把 UNKNOWN 強制歸類。**

### CVD

```text
Delta = BuyVolume - SellVolume
CVD(t) = CVD(t-1) + Delta(t)
```

另建立：CVD slope、CVD Z-score、Price/CVD divergence。

### Large Trade

**禁止固定 100 張門檻。**

第一版：

```python
large_threshold = rolling_trade_volume.quantile(0.95)
```

至少 500~2000 筆 rolling sample。
進階：結合 VolumePercentile + ValuePercentile + RelativeVolume。

### OBI

```text
OBI = (Sum(Bid1~5) - Sum(Ask1~5)) / (Sum(Bid1~5) + Sum(Ask1~5))
```

只能當輔助訊號，因為掛單可撤。

### Absorption

- Sell Absorption：大量主動 SELL，但價格跌不下去。
- Buy Absorption：大量主動 BUY，但價格漲不上去。

第一版可用：

```text
executed_volume_z / (abs(price_return_z) + epsilon)
```

並依 BUY/SELL 方向拆開。
進階加入：同價位重複成交、Bid/Ask refill、diff_bid_vol / diff_ask_vol、price level persistence。

### Trade Speed

計算 trades/sec、volume/sec、trade value/sec。全部用 rolling Z-score 正規化。

## 9. Feature Normalization

**不同股票不可直接比較張數。**

支援：rolling Z-score、percentile、turnover ratio、shares outstanding ratio、avg volume ratio。

例如：

```text
Foreign5DStrength = ForeignNet5D / AvgVolume20D
```

再做 Z-score。

## 10. Score 設計

### Intraday Score

```text
Large Trade Delta       30%
CVD                     25%
Absorption              20%
OBI                     10%
Trade Speed             10%
Price Efficiency         5%
```

### Institutional Score

```text
Investment Trust    30%
Foreign             25%
Dealer              10%
Margin Change      -15%
SBL Change         -10%
Short Change       -10%
```

> 第一版先固定權重；第二版再做 conditional weighting。

### Holder Score

```text
Large Holder Ratio Change      +60%
Retail Ratio Change            -25%
Holder Count Change            -15%
```

### Market Score

至少：TAIEX > MA20、TAIEX > MA60、MA20 slope、advance/decline、industry trend、volatility regime。轉成 -1~+1。

### Composite Chip Score

```text
Intraday      35%
Institution   30%
TDCC          25%
Market        10%
```

最後轉成 0~100。

## 11. Signal 分級

```text
>=75  STRONG / BUY Candidate
65-74 WATCH
50-64 NEUTRAL / HOLD
40-49 WEAK
<40   AVOID
```

> Chip Score ≥ 75 **仍不能直接 BUY**，必須通過 Entry Filter（見 04-decision-risk.md）。
