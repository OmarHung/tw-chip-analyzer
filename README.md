# 台股籌碼分析與進出場建議 MVP

## 目標

整合三個時間尺度：

1. 盤中：Shioaji Tick / BidAsk -> CVD、大單、OBI、Absorption、成交速度
2. 盤後：TWSE / TPEx -> 外資、投信、自營商、融資融券、借券
3. 每週：TDCC -> 大戶/散戶持股級距變化

最後產生：
- Chip Score 0~100
- BUY / WATCH / HOLD / REDUCE / EXIT / AVOID
- Entry zone
- Stop loss
- TP1 / TP2
- Reasons

## 建議規則

### BUY
- Chip Score >= 75
- 盤中 order-flow 不弱
- 不追價超過 entry_high
- 市場/產業趨勢不能極端逆風

### WATCH
- Score 65~74
- 等價格回到 entry zone 或突破確認

### EXIT
持倉中：
- Score < 40
- 或跌破 stop loss
- 或大戶/法人/盤中資金同時翻空

## 資料庫建議

- raw_tick
- raw_orderbook
- feature_1m
- daily_price
- institutional_daily
- margin_daily
- sbl_daily
- tdcc_weekly
- feature_daily
- signal_snapshot
- trade_journal

## 重要原則

不要用「單筆大單 = 大戶」。
應該看持續性與交叉驗證：

盤中 order flow
+ 盤後法人/信用交易
+ 週度 TDCC 集中度

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```

## 下一步

1. 補 TPEx connector
2. 建 PostgreSQL schema + Alembic
3. 建 1m aggregation worker
4. 建 daily feature job
5. 建 backtest engine
6. 實盤先 paper trade，不直接下單
