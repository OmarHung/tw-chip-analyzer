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
tdcc:
  retail_max_lots: 50
  large_min_lots: 400
  super_large_min_lots: 1000
```

**所有 threshold 都 config 化。**
