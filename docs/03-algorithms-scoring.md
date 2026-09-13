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

> **現況（2026-09-13，docs/12 Phase 3B）**：`raw_tick` 只有 bid/ask 價格、沒有五檔委託量，
> **目前不計算 OBI**。原本權重名為 `obi` 的其實是 CVD 斜率（每分鐘量正規化），已正式改名為
> `weights.intraday.cvd_slope` / `feature_daily.cvd_slope_norm`。`app/services/orderflow/obi.py`
> 的純函式保留給日後 realtime order book 使用。

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

> **成分可用性（2026-09-13 docs/09、docs/12）**：缺資料的成分一律排除並重分配權重，不以中性 0 灌水。
> - market：只有「目標日」MarketDaily 存在才啟用；缺當日不沿用前一日（未知）。
> - institutional：子項缺值在成分內依 |權重| 重分配；有效子項（權重非 0）全缺則排除整個成分。
> - 百分位映射依 `(components, availability_signature)` 分組；signature 含 institutional 實際參與子項，
>   子項覆蓋率不同（例如 TPEx 融資來源失敗）不混排。零權重的 SBL 不切組；單檔小組退回 linear 映射。
> - 橫斷面 z 先截尾（`features.winsorize_quantile`）再夾 `features.z_clip`，單一離群值不壓扁因子。

### Intraday Score

```text
Large Trade Delta       30%
CVD                     25%
Absorption              20%
CVD Slope（原誤稱 OBI） 10%
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

### 成分缺資料時的權重重分配（實作補充）

Phase 1 尚無盤中 intraday 即時資料。若把缺資料成分以中性值（50）代入，會把
整體分數往中間灌水、壓低有效訊號（例：法人+大戶皆強仍打不到 BUY）。

依 OECD《Handbook on Constructing Composite Indicators》標準做法：**某成分在
該期完全無資料時，將其排除並把權重按比例重分配給其餘成分**，使總權重仍為 1。

實作於 `ChipScorer.score(..., active_components=...)`；`AnalysisService`（Phase 1）
傳入 `{"institutional","holder","market"}`（排除 intraday）。待 Phase 3 接上
Shioaji realtime 後，intraday 自然納入，無需改動評分邏輯。

參考：[OECD Handbook on Constructing Composite Indicators](https://www.oecd.org/content/dam/oecd/en/publications/reports/2008/08/handbook-on-constructing-composite-indicators-methodology-and-user-guide_g1gh9301/9789264043466-en.pdf)

## 11. Signal 分級

```text
>=75  STRONG / BUY Candidate
65-74 WATCH
50-64 NEUTRAL / HOLD
40-49 WEAK
<40   AVOID
```

> Chip Score ≥ 75 **仍不能直接 BUY**，必須通過 Entry Filter（見 04-decision-risk.md）。
