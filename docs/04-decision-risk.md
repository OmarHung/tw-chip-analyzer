# 04 · 決策引擎：Entry / Risk / Exit

> 對應原始交接文件 §12–14

## 12. Entry Filter

BUY 條件：

```text
ChipScore >= 75
AND RiskReward >= 2
AND 價格不過度偏離 VWAP / MA20
AND Market Regime != Strong Bear
AND 流動性達最低要求
AND 非鎖死漲停等不可合理成交狀況
```

若 Score ≥ 75 但價格過度延伸，輸出 **WATCH**。

## 13. Risk Engine

ATR14 使用 **Daily timeframe**。

初版停損：

```text
min(
    entry - 1.5 * ATR,
    recent_swing_low - 0.2 * ATR
)
```

最大停損百分比：6%，config 化。

Take Profit：

```text
TP1 = 2R
TP2 = 3R
R = Entry - StopLoss
```

**禁止固定 +5% / +10%。**

## 14. Exit Logic

Hard Stop：`price <= stop_loss => EXIT`。

Chip deterioration：

```text
ChipScore < 50 => REDUCE
ChipScore < 40 => EXIT
```

Distribution Warning：

```text
Price new high
AND CVD declining
AND LargeTradeDelta negative
AND BuyAbsorption increasing
```

=> REDUCE。

TP1 後可啟用 ATR trailing stop。
