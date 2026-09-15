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

> **EOD 起跑時間與延後重試**（`schedule.eod`）：平日 **16:00** 起跑（原 14:30；後移是為了替
> 法人、融資券、借券與 TPEx 報表留上線緩衝）。跑完後檢查行情／法人／融資券的 TWSE+TPEx 合計
> 筆數是否達 `completeness` 門檻；**不完整**、或因手動回補／系統頁腳本佔用單飛鎖而**根本沒跑成**，
> 都會每 `retry.interval_minutes`（15 分）重試一次，直到 `retry.deadline_hour:deadline_minute`
> （18:00）為止，到點仍不完整才放棄。「今天」一律以 `schedule.timezone` 換算，不依賴主機時區。

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
> | `heatmap.market / industry / stock` | 熱力圖展示參數（treemap 方塊數上限、產業矩陣天數與成分股門檻、個股分項天數）。**純展示層**，不在 `data_version` 區塊內，改動不觸發分數重建提示 |
> | `schedule.eod.completeness / retry` | EOD 完整性檢查（行情/法人/融資券最低筆數）與重試節流（不完整或忙碌跳過時，每 N 分鐘重試至截止時間） |
> | `notify.telegram` | 每日推播「新進 BUY / AVOID」（`app.jobs.notify_signals`，EOD 資料完整後自動送；到截止時間仍不完整也會送，但附警示；補算過去日期不送）。「新進」＝與該檔回看窗內最近一筆快照的 action 不同。**可在 `/settings`「Telegram 推播」卡片設定**（token、chat id〔可按「偵測」自動列出〕、啟用、推播類型、每類檔數、原因條數、最低成交金額；存 DB `notify_setting`，欄位 NULL＝沿用 `.env` 的 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 與此 YAML；token 對外一律遮蔽、稽核 log 只記欄位名、httpx 請求 log 已遮蔽 URL 內的 token；寫入需 `OPS_API_KEY`）。`lookback_days`／分段長度／逾時重試只在 YAML。推播失敗只記 log，不影響 EOD。**純通知層**，不在 `data_version` 區塊內 |
> | `weights.intraday.cvd_slope` | 原 `obi`（改名，非 OBI） |
> | `mops.http / schedule / insider_holding / transfer_declaration / backfill / research` | Phase 2 MOPS（shadow-only，docs/14）：重試節流、排程（2026-09-14 線上 smoke 驗收後 `enabled: true`；某日轉讓網頁失敗應以 `app.jobs.mops <該日> --skip-holdings` 補跑，勿用回補腳本）、揭露落後規則、轉讓回看窗、回補範圍、regime 分層、honest OOS 最低 test 日數（`research.min_honest_test_days`）。不在 `data_version` 區塊內，改動不觸發正式分數重建提示 |
>
> **UI 覆寫**：`/settings` 頁可調整 `app/core/threshold_registry.py` 登錄的鍵，覆寫值存 DB
> `threshold_override`（YAML 仍是預設與結構），修改歷史存 `threshold_change`。每鍵標示「即時生效」或
> 「需重建」；未經 OOS 驗證的權重（SBL、產業趨勢）鎖定需解鎖。改「需重建」類設定後，設定頁以
> `signal_snapshot.config_version` 判斷並提示重建。
>
> **腳本按鈕**：`/system` 頁「工作」可觸發 `app/jobs/tasks.py` 白名單腳本（子行程 + nice，與 EOD/回補
> 共用單飛鎖；工作進行中到 EOD 時間，當日 EOD 會延後重試——每 15 分鐘再試一次，最晚到 18:00，
> 不再像先前直接跳過整天）。寫入型操作需 `OPS_API_KEY`。
