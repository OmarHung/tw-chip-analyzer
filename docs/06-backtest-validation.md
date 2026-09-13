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

### 17.1 實作口徑（2026-09-13，docs/09、docs/12）

- **價格**：一律用 `app.backtest.runner.load_bars` 的後復權 OHLC（拆股/除權息日不出現假報酬）。
- **進場**：訊號日的**市場次一交易日** open；該檔當日停牌則丟棄訊號（不以數週後復牌日進場）。
  市場交易日曆 = 全部標的 bar 日期聯集（`market_calendar`）。
- **前瞻驗證頁** `/validation` 與離線 backtest 同口徑（測試保證逐 bucket 一致）。
- **統計**：逐日 rank IC 的 t 值用 **Newey–West**（lag = horizon − 1），樸素 t 另列 `ic_t_naive` 僅供對照。
  重疊的 k 日報酬會讓樸素 t 高估顯著性（實測舊分數 20D：樸素 2.31 → NW 1.50）。
- **快取**：key = `signal_snapshot` / `daily_price` / `corporate_action` 的 `max(updated_at)` + 筆數 +
  報告設定 hash（`backtest` 全部 + `validation.min_ic_names`，與計算共用同一份解析值）；
  重建覆寫同日期同筆數、或 reload 後只改任一報告設定，都會失效，不需重啟 API。
- **pending**：`pending_entry` = 最新交易日訊號（尚無下一交易日）；`pending_exit_min_horizon` = 已進場但
  最短 horizon 出場價未出現。
- **重建後的歷史分數屬樣本內**（算法看過這段資料後修正）；誠實 OOS 從修正部署後每日 EOD 新寫入的
  snapshot 開始累積。`signal_snapshot.config_version` 記錄產生當下的設定版本。

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
