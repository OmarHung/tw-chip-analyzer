# 06 · Backtest、Look-ahead 防護、Testing、Paper Trading

> 對應原始交接文件 §17、§18、§22、§23

## 17. Backtest 是 MVP 必要條件

**實盤前必須完成。**

每個 signal 計算：

```text
Forward 1D / 3D / 5D / 10D / 20D Return
MFE
MAE
```

第一版成功定義可用：

```text
5D MFE >= 6%
AND 5D MAE <= 4%
```

Backtest Metrics：Signal Count、Win Rate、Avg / Median Return、Profit Factor、Expectancy、Max Drawdown、MFE / MAE、Sharpe / Sortino。

必須比較 score threshold：60/65/70/75/80/85。

> 理想現象是 Score 越高，forward return 越好，MAE 不惡化。

## 18. Look-ahead Bias 防護

每筆資料保存：

```text
data_date
available_at
```

Backtest 必須依 `available_at` 決定當時是否可使用。

**禁止：**
- 使用尚未公布的法人資料
- TDCC 週資料提前使用
- 盤中偷看當日收盤資料
- 任何 future leakage

## 22. Testing

**Unit Tests**：aggressor side、CVD、large trade threshold、OBI、absorption、Z-score、Chip Score、risk calculation、signal thresholds。

**Integration Tests**：TWSE importer、TPEx importer、TDCC importer、Shioaji parser、DB insert、Scanner API。

## 23. Paper Trading

實盤前建立：`paper_position`、`paper_order`、`paper_trade`。

模擬：Entry、Stop、TP、Fee、Tax、Slippage。

> 交易成本必須 config 化，**禁止使用 0 成本回測推論策略有效。**
