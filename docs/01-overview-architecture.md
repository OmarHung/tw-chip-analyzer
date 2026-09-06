# 01 · 專案目標與系統架構

> 對應原始交接文件 §1–5

## 1. 專案目標

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
- Intraday Score / Institutional Score / Holder Score / Market Score
- BUY / WATCH / HOLD / REDUCE / EXIT / AVOID
- 建議進場區間、停損價、TP1 / TP2、Risk/Reward
- 訊號形成原因、籌碼轉強/轉弱原因、歷史訊號績效

**第一階段只提供「分析與建議」，禁止直接串自動下單。**

## 2. 核心設計原則

### 2.1 籌碼與進場時機分離

- Chip Analysis 回答：籌碼目前偏多還是偏空？
- Trade Decision 回答：現在是否有合理風險報酬可以進場？

範例：

```text
Chip Score = 85
股價離 MA20 +15%
ATR 過大
Risk/Reward < 1.5
```

應輸出 **WATCH**，而不是 BUY。

### 2.2 不把單一訊號當成大戶證據

禁止：

```text
大單成交 = 大戶
OBI 高 = 一定漲
外資買超 = 一定漲
TDCC 大戶增加 = 明天會漲
```

系統判斷必須依賴「多個相對獨立訊號 + 價格結構 + 風險報酬」。

## 3. 系統架構

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

## 4. 技術選型

**Backend**：Python 3.12+、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、asyncio、httpx、pandas、numpy、scipy、psycopg3

**Realtime**：
- Phase 1：asyncio Queue
- Production 可升級 Redis Streams / NATS / Kafka
- 不建議第一版直接上 Kafka

**Database**：PostgreSQL 16，可選 TimescaleDB

**Frontend**：Next.js 15+、TypeScript、React、Highcharts 或 TradingView Lightweight Charts

## 5. 建議專案目錄

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
