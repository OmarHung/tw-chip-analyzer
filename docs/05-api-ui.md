# 05 · API 與 UI

> 對應原始交接文件 §15–16

## 15. API

### GET /api/stocks/{symbol}/analysis

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

### GET /api/scanner

支援 query：`min_score`、`action`、`min_turnover`、`industry`、`limit`。依 score 排序。

## 16. UI

**Dashboard**：TAIEX、Market Regime、上漲/下跌家數、BUY candidate 數、Distribution Warning 數。

**Scanner 欄位**：

```text
股票 / 價格 / Chip / Intraday / 法人 / TDCC / Action / Volume / RR
```

**Stock Detail**：
- OHLC + VWAP + MA20 + Signal marker
- CVD
- Large Trade Delta
- OBI
- Absorption
- Foreign / Trust
- Margin
- TDCC Holder Ratio
- Signal Reasons
