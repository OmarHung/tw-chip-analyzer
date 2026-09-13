# 07 · 排程與 Config

> 對應原始交接文件 §19、§21

## 19. 排程

### 盤後流程

```text
TWSE/TPEx price
→ Institutional
→ Margin
→ SBL
→ Daily Features
→ Daily Chip Score
→ Signal Snapshot
```

> 時間全部 config 化，不硬編碼。

### TDCC 每週

```text
Fetch raw
→ Save
→ Build holder summary
→ Weekly feature
→ Holder score
```

### Realtime

```text
Shioaji callback
→ Queue
→ Processor
→ Tick classification
→ OrderFlow metrics
→ 1m aggregator
→ Feature DB
→ Intraday score
→ Signal engine
```

> **Shioaji callback 禁止做 heavy DB、pandas、HTTP、scoring。**

## 21. Config

```yaml
signal:
  buy_score: 75
  watch_score: 65
  reduce_score: 50
  exit_score: 40
risk:
  atr_stop_multiplier: 1.5
  swing_buffer_atr: 0.2
  max_stop_pct: 0.06
  tp1_r: 2.0
  tp2_r: 3.0
large_trade:
  percentile: 0.95
  min_samples: 500
intraday_batch:              # 批次逐筆匯入範圍（app.jobs.import_ticks）
  max_symbols: 200           # 依當日 turnover 由高到低取前 N 檔
  min_turnover: 20000000     # 成交金額下限（TWD）
  throttle_ms: 200           # 每檔之間節流（毫秒）
  usage_stop_pct: 95         # Shioaji api.usage() 用量達此 % 即停批次
tdcc:
  retail_max_lots: 50
  large_min_lots: 400
  super_large_min_lots: 1000
```

**所有 threshold 都 config 化。**

> 上方為早期節錄；**完整且為準的設定見 `config/thresholds.yaml`**（每個鍵有註解）。2026-09-13 起新增：
>
> | 區塊 | 用途 |
> |---|---|
> | `features.flow_window_bars / ma_bars / atr_period / swing_low_bars / sbl_lookback_bars / industry_min_members` | 特徵固定視窗（不足即 NULL） |
> | `features.price_window_days / winsorize_quantile / z_clip` | 價格載入視窗、橫斷面 z 截尾 |
> | `risk.resistance_lookback_bars / resistance_min_bars / breakout_target_atr / min_risk_pct` | RR 目標與風險分母 |
> | `entry_filter.limit_lock_min_change_pct` | 鎖死漲停判定 |
> | `exit.distribution_enabled` | 出貨警示開關（預設 false） |
> | `market_regime.ma_short_bars / ma_long_bars / slope_lookback_bars / volatility_bars` | 大盤 regime 視窗 |
> | `validation.min_ic_names` | 前瞻驗證每日 IC 最低檔數 |
> | `intraday_batch.max_consecutive_failures` | 逐筆批次連續失敗停止 |
> | `jobs.nice / output_max_chars / cancel_grace_sec` | 系統頁腳本按鈕子行程 |
> | `weights.intraday.cvd_slope` | 原 `obi`（改名，非 OBI） |
>
> **UI 覆寫**：`/settings` 頁可調整 `app/core/threshold_registry.py` 登錄的鍵，覆寫值存 DB
> `threshold_override`（YAML 仍是預設與結構），修改歷史存 `threshold_change`。每鍵標示「即時生效」或
> 「需重建」；未經 OOS 驗證的權重（SBL、產業趨勢）鎖定需解鎖。改「需重建」類設定後，設定頁以
> `signal_snapshot.config_version` 判斷並提示重建。
>
> **腳本按鈕**：`/system` 頁「工作」可觸發 `app/jobs/tasks.py` 白名單腳本（子行程 + nice，與 EOD/回補
> 共用單飛鎖；工作進行中到 EOD 時間，當日 EOD 會被跳過）。寫入型操作需 `OPS_API_KEY`。
