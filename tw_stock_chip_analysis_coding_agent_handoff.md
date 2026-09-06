# 台股籌碼分析與進出場建議系統
## Coding Agent 開發交接文件

版本：v1.0  
狀態：可進入實作  
主要語言：Python 3.12+  
資料庫：PostgreSQL（可選 TimescaleDB）  
即時行情：永豐金 Shioaji  
盤後資料：TWSE / TPEx / TDCC / MOPS  
API：FastAPI  
前端建議：Next.js + TypeScript  

---

# 1. 專案目標

建立一套「台股籌碼分析與進出場建議系統」。

整合：

1. 盤中逐筆成交與五檔委託
2. 三大法人買賣超
3. 融資融券
4. 借券
5. TDCC 股權分散
6. 價量與波動
7. 市場/產業環境
8. 未來可加入券商分點與 MOPS 內部人資訊

最終輸出：

- Chip Score：0~100
- Intraday Score
- Institutional Score
- Holder Score
- Market Score
- BUY / WATCH / HOLD / REDUCE / EXIT / AVOID
- 建議進場區間
- 停損價
- TP1 / TP2
- Risk/Reward
- 訊號形成原因
- 籌碼轉強/轉弱原因
- 歷史訊號績效

第一階段只提供「分析與建議」，禁止直接串自動下單。

---

# 2. 核心設計原則

## 2.1 籌碼與進場時機分離

Chip Analysis 回答：

> 籌碼目前偏多還是偏空？

Trade Decision 回答：

> 現在是否有合理風險報酬可以進場？

例如：

```text
Chip Score = 85
股價離 MA20 +15%
ATR 過大
Risk/Reward < 1.5
```

應輸出 WATCH，而不是 BUY。

## 2.2 不把單一訊號當成大戶證據

禁止：

```text
大單成交 = 大戶
OBI 高 = 一定漲
外資買超 = 一定漲
TDCC 大戶增加 = 明天會漲
```

系統判斷必須依賴「多個相對獨立訊號 + 價格結構 + 風險報酬」。

---

# 3. 系統架構

```text
                          ┌─────────────────┐
                          │     Shioaji     │
                          │ Tick / BidAsk   │
                          └────────┬────────┘
                                   │
                            Real-time Collector
                                   │
                      ┌────────────┴────────────┐
                      │                         │
                 raw_tick                raw_orderbook
                      │                         │
                      └────────────┬────────────┘
                                   │
                            Feature Aggregator
                                   │
                    feature_1s / feature_1m / 5m
                                   │
                                   ▼
                          Intraday Flow Engine
                    CVD / Large Trade / OBI /
                    Absorption / Trade Speed
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
        ▼                          ▼                          ▼
    TWSE / TPEx                  TDCC                     OHLCV
法人/融資/借券               股權分散                  價格/ATR/VWAP
        │                          │                          │
        └──────────────────────────┼──────────────────────────┘
                                   ▼
                             Feature Store
                                   │
                                   ▼
                              Chip Engine
                                   │
                                   ▼
                            Trade Decision
                                   │
                                   ▼
                            Signal Snapshot
                                   │
                   ┌───────────────┴───────────────┐
                   ▼                               ▼
                FastAPI                       Backtester
                   │
                   ▼
               Next.js UI
```

---

# 4. 技術選型

Backend：

- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy 2
- Alembic
- asyncio
- httpx
- pandas
- numpy
- scipy
- psycopg3

Realtime：

- Phase 1：asyncio Queue
- Production 可升級 Redis Streams / NATS / Kafka
- 不建議第一版直接上 Kafka

Database：

- PostgreSQL 16
- 可選 TimescaleDB

Frontend：

- Next.js 15+
- TypeScript
- React
- Highcharts 或 TradingView Lightweight Charts

---

# 5. 建議專案目錄

```text
app/
├── api/
│   ├── stocks.py
│   ├── signals.py
│   ├── scanner.py
│   └── backtests.py
├── connectors/
│   ├── shioaji/
│   ├── twse/
│   ├── tpex/
│   ├── tdcc/
│   └── mops/
├── domain/
│   ├── features.py
│   ├── signal.py
│   ├── position.py
│   └── enums.py
├── services/
│   ├── orderflow/
│   │   ├── aggressor.py
│   │   ├── cvd.py
│   │   ├── large_trade.py
│   │   ├── obi.py
│   │   ├── absorption.py
│   │   └── trade_speed.py
│   ├── chip/
│   │   ├── intraday_score.py
│   │   ├── institutional_score.py
│   │   ├── holder_score.py
│   │   ├── market_score.py
│   │   └── composite.py
│   ├── decision/
│   │   ├── entry.py
│   │   ├── exit.py
│   │   └── risk.py
│   └── scanner/
├── jobs/
├── repositories/
├── backtest/
├── db/
└── main.py
```

---

# 6. 資料來源與責任

## Shioaji

用途：

- Tick
- BidAsk 五檔
- 盤中 order flow

注意：只能知道市場行為，不能辨認成交者身分。

## TWSE / TPEx

用途：

- OHLCV
- 三大法人
- 融資融券
- 借券

## TDCC

用途：

- 股權分散
- 大戶/散戶持股比例變化

## MOPS

Phase 2：

- 董監持股
- 大股東
- 質押
- 持股轉讓

## 券商分點

Phase 3 才加入。不可假設存在免費完整歷史 API。

---

# 7. 主要資料表

## raw_tick

```sql
CREATE TABLE raw_tick (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(16) NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    price NUMERIC(14,4) NOT NULL,
    volume INTEGER NOT NULL,
    bid1_price NUMERIC(14,4),
    ask1_price NUMERIC(14,4),
    aggressor_side SMALLINT,
    trade_value NUMERIC(18,2),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_raw_tick_symbol_ts ON raw_tick(symbol, ts DESC);
```

aggressor_side：1=BUY、-1=SELL、0=UNKNOWN。

## raw_orderbook

保存 Bid1~Bid5 / Ask1~Ask5 的價量快照。Production 不建議永久保留全部 snapshot。

## feature_1m

```sql
CREATE TABLE feature_1m (
    symbol VARCHAR(16) NOT NULL,
    bucket TIMESTAMPTZ NOT NULL,
    open NUMERIC(14,4),
    high NUMERIC(14,4),
    low NUMERIC(14,4),
    close NUMERIC(14,4),
    volume BIGINT,
    buy_volume BIGINT,
    sell_volume BIGINT,
    delta BIGINT,
    cvd BIGINT,
    trade_count INTEGER,
    avg_trade_size NUMERIC(16,4),
    large_buy_volume BIGINT,
    large_sell_volume BIGINT,
    large_trade_delta BIGINT,
    obi NUMERIC(10,6),
    sell_absorption_score NUMERIC(10,6),
    buy_absorption_score NUMERIC(10,6),
    trade_speed NUMERIC(16,6),
    vwap NUMERIC(14,4),
    PRIMARY KEY(symbol, bucket)
);
```

## institutional_daily

保存外資、投信、自營商自行買賣、自營商避險。自營商兩類必須拆開。

## margin_daily

保存融資買進/賣出/餘額與融券賣出/回補/餘額。

## sbl_daily

保存借券賣出、還券、餘額。

## tdcc_weekly

保存原始級距。

## tdcc_summary_weekly

聚合：

- retail_ratio
- medium_ratio
- large_ratio
- super_large_ratio
- holder_count

第一版：Retail <= 50 張；Large >= 400 張；Super Large >= 1000 張。所有級距必須 config 化。

## signal_snapshot

每個訊號都保存完整 feature payload，供回測與未來 ML 使用。

---

# 8. Order Flow 演算法

## Aggressor Side

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

禁止把 UNKNOWN 強制歸類。

## CVD

```text
Delta = BuyVolume - SellVolume
CVD(t) = CVD(t-1) + Delta(t)
```

另建立：

- CVD slope
- CVD Z-score
- Price/CVD divergence

## Large Trade

禁止固定 100 張門檻。

第一版：

```python
large_threshold = rolling_trade_volume.quantile(0.95)
```

至少 500~2000 筆 rolling sample。

進階：結合 VolumePercentile + ValuePercentile + RelativeVolume。

## OBI

```text
OBI = (Sum(Bid1~5) - Sum(Ask1~5)) / (Sum(Bid1~5) + Sum(Ask1~5))
```

只能當輔助訊號，因為掛單可撤。

## Absorption

Sell Absorption：大量主動 SELL，但價格跌不下去。  
Buy Absorption：大量主動 BUY，但價格漲不上去。

第一版可用：

```text
executed_volume_z / (abs(price_return_z) + epsilon)
```

並依 BUY/SELL 方向拆開。

進階加入：

- 同價位重複成交
- Bid/Ask refill
- diff_bid_vol / diff_ask_vol
- price level persistence

## Trade Speed

計算：

- trades/sec
- volume/sec
- trade value/sec

全部用 rolling Z-score 正規化。

---

# 9. Feature Normalization

不同股票不可直接比較張數。

支援：

- rolling Z-score
- percentile
- turnover ratio
- shares outstanding ratio
- avg volume ratio

例如：

```text
Foreign5DStrength = ForeignNet5D / AvgVolume20D
```

再做 Z-score。

---

# 10. Score 設計

## Intraday Score

```text
Large Trade Delta       30%
CVD                     25%
Absorption              20%
OBI                     10%
Trade Speed             10%
Price Efficiency         5%
```

## Institutional Score

```text
Investment Trust    30%
Foreign             25%
Dealer              10%
Margin Change      -15%
SBL Change         -10%
Short Change       -10%
```

第一版先固定權重；第二版再做 conditional weighting。

## Holder Score

```text
Large Holder Ratio Change      +60%
Retail Ratio Change            -25%
Holder Count Change            -15%
```

## Market Score

至少：

- TAIEX > MA20
- TAIEX > MA60
- MA20 slope
- advance/decline
- industry trend
- volatility regime

轉成 -1~+1。

## Composite Chip Score

```text
Intraday      35%
Institution   30%
TDCC          25%
Market        10%
```

最後轉成 0~100。

---

# 11. Signal 分級

```text
>=75  STRONG / BUY Candidate
65-74 WATCH
50-64 NEUTRAL / HOLD
40-49 WEAK
<40   AVOID
```

Chip Score >=75 仍不能直接 BUY，必須通過 Entry Filter。

---

# 12. Entry Filter

BUY 條件：

```text
ChipScore >= 75
AND RiskReward >= 2
AND 價格不過度偏離 VWAP / MA20
AND Market Regime != Strong Bear
AND 流動性達最低要求
AND 非鎖死漲停等不可合理成交狀況
```

若 Score >=75 但價格過度延伸，輸出 WATCH。

---

# 13. Risk Engine

ATR14 使用 Daily timeframe。

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

禁止固定 +5% / +10%。

---

# 14. Exit Logic

Hard Stop：price <= stop_loss => EXIT。

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

---

# 15. API

## GET /api/stocks/{symbol}/analysis

Response 範例：

```json
{
  "symbol": "2330",
  "price": 1000,
  "chip_score": 82.4,
  "scores": {
    "intraday": 88.2,
    "institutional": 76.1,
    "holder": 80.3,
    "market": 65.0
  },
  "action": "BUY",
  "entry": {"low": 992, "high": 1005},
  "risk": {
    "stop_loss": 965,
    "tp1": 1070,
    "tp2": 1105,
    "rr": 2.0
  },
  "reasons": [
    "盤中大額主動買盤偏強",
    "CVD 持續走高",
    "TDCC 大戶持股增加",
    "融資下降"
  ]
}
```

## GET /api/scanner

支援 query：

- min_score
- action
- min_turnover
- industry
- limit

依 score 排序。

---

# 16. UI

Dashboard：

- TAIEX
- Market Regime
- 上漲/下跌家數
- BUY candidate 數
- Distribution Warning 數

Scanner 欄位：

```text
股票 / 價格 / Chip / Intraday / 法人 / TDCC / Action / Volume / RR
```

Stock Detail：

- OHLC + VWAP + MA20 + Signal marker
- CVD
- Large Trade Delta
- OBI
- Absorption
- Foreign / Trust
- Margin
- TDCC Holder Ratio
- Signal Reasons

---

# 17. Backtest 是 MVP 必要條件

實盤前必須完成。

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

Backtest Metrics：

- Signal Count
- Win Rate
- Avg / Median Return
- Profit Factor
- Expectancy
- Max Drawdown
- MFE / MAE
- Sharpe / Sortino

必須比較 score threshold：60/65/70/75/80/85。

理想現象是 Score 越高，forward return 越好，MAE 不惡化。

---

# 18. Look-ahead Bias 防護

每筆資料保存：

```text
data_date
available_at
```

Backtest 必須依 available_at 決定當時是否可使用。

禁止：

- 使用尚未公布的法人資料
- TDCC 週資料提前使用
- 盤中偷看當日收盤資料
- 任何 future leakage

---

# 19. 排程

盤後流程：

```text
TWSE/TPEx price
→ Institutional
→ Margin
→ SBL
→ Daily Features
→ Daily Chip Score
→ Signal Snapshot
```

時間全部 config 化，不硬編碼。

TDCC 每週：

```text
Fetch raw
→ Save
→ Build holder summary
→ Weekly feature
→ Holder score
```

Realtime：

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

Shioaji callback 禁止做 heavy DB、pandas、HTTP、scoring。

---

# 20. Tick 寫入策略

禁止每 Tick 一次 transaction commit。

使用 batch：

- 100~1000 rows
- 或 100~500ms flush

可用 COPY / bulk insert。

Retention：

```text
raw_tick       30~90 days
raw_orderbook   7~30 days
feature_1m      永久
daily           永久
weekly          永久
```

---

# 21. Config

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

所有 threshold 都 config 化。

---

# 22. Testing

Unit Tests：

- aggressor side
- CVD
- large trade threshold
- OBI
- absorption
- Z-score
- Chip Score
- risk calculation
- signal thresholds

Integration Tests：

- TWSE importer
- TPEx importer
- TDCC importer
- Shioaji parser
- DB insert
- Scanner API

---

# 23. Paper Trading

實盤前建立：

- paper_position
- paper_order
- paper_trade

模擬：

- Entry
- Stop
- TP
- Fee
- Tax
- Slippage

交易成本必須 config 化，禁止使用 0 成本回測推論策略有效。

---

# 24. ML 不在 Phase 1

禁止一開始投入：

- LSTM
- Transformer
- PPO
- RL

先建立 rule-based baseline。

累積足夠歷史資料後，Phase 4 再用 LightGBM / XGBoost。

Features 可包含：

```text
CVD_Z
LargeTrade_Z
OBI
Absorption
TradeSpeed
Foreign5D
Trust5D
MarginChange
SBLChange
LargeHolderChange
RetailHolderChange
MarketTrend
IndustryTrend
ATR
VolumeRatio
```

Labels：

```text
forward_return_5d
mfe_5d
mae_5d
success
```

---

# 25. 開發 Phase

## Phase 1：Daily Chip Scanner

先完成：

- TWSE
- TPEx
- TDCC
- OHLCV
- Institutional
- Margin
- SBL
- PostgreSQL
- Feature Engine
- Chip Score
- Scanner API
- Backtest

目標：先證明盤後籌碼模型有效。

## Phase 2

加入 MOPS 內部人 / 大股東 / 質押。

## Phase 3

加入 Shioaji realtime：CVD / Large Trade / OBI / Absorption / Trade Speed。

## Phase 4

加入券商分點與 ML Probability。

---

# 26. Coding Agent 開發順序

1. 建 FastAPI / SQLAlchemy / Alembic / PostgreSQL / config / logging。
2. 建 DB schema。
3. 實作 TWSE / TPEx / TDCC importer。
4. 建 daily / weekly feature。
5. 建 rule-based Chip Score。
6. 建 Scanner API。
7. 建 Backtest，先驗證 Daily + TDCC。
8. 接 Shioaji realtime。
9. 加 CVD / Large Trade / OBI / Absorption。
10. 建 Intraday Score。
11. 整合 BUY / WATCH / EXIT。
12. 建 Next.js UI。
13. Paper Trading。

不要一開始同時實作全部功能。

---

# 27. MVP Definition of Done

Data：

- [ ] 上市股票基本資料同步
- [ ] 上櫃股票基本資料同步
- [ ] Daily OHLCV
- [ ] 三大法人
- [ ] 融資融券
- [ ] 借券
- [ ] TDCC
- [ ] Shioaji Tick
- [ ] Shioaji BidAsk

Feature：

- [ ] CVD
- [ ] Large Trade
- [ ] OBI
- [ ] Absorption
- [ ] Trade Speed
- [ ] Institutional Z-score
- [ ] TDCC change
- [ ] Market trend

Signal：

- [ ] Chip Score
- [ ] BUY
- [ ] WATCH
- [ ] HOLD
- [ ] REDUCE
- [ ] EXIT
- [ ] AVOID

Risk：

- [ ] Entry Zone
- [ ] Stop Loss
- [ ] TP1
- [ ] TP2
- [ ] RR

Backtest：

- [ ] 1D / 3D / 5D / 10D / 20D
- [ ] MFE / MAE
- [ ] Transaction Costs
- [ ] Look-ahead bias checks

UI：

- [ ] Scanner
- [ ] Stock Detail
- [ ] Signal Reasons
- [ ] Price/CVD/Institutional/TDCC 圖表

---

# 28. 第一階段成功標準

真正成功條件不是 UI 或功能數量，而是：

> Chip Score 越高，未來報酬應有統計上的單調改善。

理想示意：

```text
Score       5D Avg Return
50-60       +0.3%
60-70       +0.8%
70-80       +1.5%
80-90       +2.6%
90+         +4.1%
```

同時 MAE 不應顯著惡化。

如果沒有這種現象，優先重新調整 feature / normalization / weight，不要急著增加新功能。

---

# 29. Existing Starter Project

目前已有初版：

```text
tw_chip_analyzer_mvp.zip
```

已包含：

- Shioaji adapter
- TWSE connector
- TDCC connector
- CVD
- Large Trade
- OBI
- Chip Scoring
- Risk Engine
- Decision Engine

Coding Agent 可參考，但不可視為 production-ready。

優先補：

- DB layer
- async architecture
- reconnect
- retry
- validation
- config
- tests
- observability

---

# 30. 每個 Milestone 的交付格式

Coding Agent 每完成一個 milestone 必須輸出：

1. 已完成項目
2. DB migration
3. API 文件
4. 測試結果
5. 尚未完成項目
6. 技術債
7. 下一步
8. 是否存在 look-ahead bias
9. 是否存在資料缺漏
10. Backtest 結果

禁止在沒有 Backtest 證據前聲稱策略有效。

---

# 31. 給 Coding Agent 的第一個直接任務

請先實作 Phase 1：Daily Chip Scanner。

第一個 milestone 只做：

```text
TWSE / TPEx / TDCC
OHLCV
Institutional
Margin
SBL
PostgreSQL
Feature Engine
Chip Score
Scanner API
Backtest
```

驗收條件：

- 可以指定日期重建全市場 daily feature
- 可以掃出 score >= 指定門檻的股票
- 可以查看 score breakdown 與 reasons
- 可以跑 1/3/5/10/20 日 forward return 回測
- 可以確認無 look-ahead bias
- 可以輸出各 score bucket 的績效比較

在此 milestone 驗證前，不要投入 Shioaji realtime UI 或 ML。

