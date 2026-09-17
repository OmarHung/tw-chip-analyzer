# 05 · API 與 UI

> 對應原始交接文件 §15–16

## 15. API

> **認證**：除 `/health` 與 `/api/auth/*` 外，所有端點都需要通過認證
> （session cookie 或 `X-Ops-Key`；尚未建立帳號時走相容模式）。寫入型端點另需 admin 角色。
> 規則、端點清單與啟用步驟見 [17-auth-and-permissions.md](17-auth-and-permissions.md)。

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

### GET /api/heatmap/market · /api/heatmap/industry · /api/heatmap/stock/{symbol}

熱力圖三個端點（`app/api/heatmap.py`）。不新增任何特徵計算，只是把既有資料換一種
密度更高的呈現；門檻走 `config/thresholds.yaml` 的 `heatmap` 區塊（純展示層，不在
`threshold_registry._DATA_SECTIONS` 內，改動不觸發分數重建提示）。

| 端點 | 內容 | query |
|---|---|---|
| `/market` | 全市場扁平列（含 `industry`／`turnover`／三個顏色維度）＋ `covered_turnover` 涵蓋率 | `limit`、`min_turnover` |
| `/industry` | 產業 × 交易日矩陣，每格為該產業成分股的**中位數**（`ret`／`score`／`inst` 一次給齊） | `days`、`min_symbols` |
| `/stock/{symbol}` | 個股籌碼分項 × 交易日（讀已落地的 `signal_snapshot`，不重算） | `days` |

兩個刻意的設計：**中位數而非平均**（產業內一兩檔漲停會讓平均看起來像整個產業在動）；
**成分股數門檻**（`heatmap.industry.min_symbols`，與 feature_builder 的 industry_trend 同為 5——
樣本太少的中位數是雜訊，寧可留白也不要畫出會被誤讀的顏色）。缺成分一律傳 NULL，由前端
畫成「無資料」斜線紋，與「中性」分開。

第四張熱力圖（分數 bucket × horizon）沒有專用端點——數字已由 `/api/validation/forward` 供應。

## 16. UI

**Dashboard**：TAIEX（收盤＋漲跌點數/幅度）、台指期主力月份（收盤＋漲跌點數/幅度）、
Market Regime、均線 MA20/MA60、上漲/下跌家數、BUY candidate 數、Distribution Warning 數、
市場熱力圖（treemap）、產業輪動矩陣。

Hero 列的漲跌：TAIEX 漲跌由 `market_index` 前一交易日收盤算（前收缺則 NULL，不猜）；
台指期直接用期交所給的漲跌價/漲跌%（見 `docs/02` 的 `futures_daily`）。兩者都走台股語意
紅漲綠跌（`dirColor`）。

**熱力圖（四張）**：

| 位置 | 元件 | 形式 | 顏色維度 |
|---|---|---|---|
| 總覽 | `MarketTreemap` | 產業分組 treemap，方塊面積＝成交值 | 漲跌幅／籌碼分數／法人強度（可切） |
| 總覽 | `IndustryHeatmap` | 產業 × 交易日矩陣（中位數） | 同上 |
| 個股 | `StockScoreHeatmap` | 五列（總分＋四分項）× 交易日 | 分數（無漲跌維度，故不給切換） |
| 驗證 | `ValidationHeatmap` | 分數 bucket × horizon | 平均淨報酬 |

色階規則（`lib/heat.ts`）：報酬類走 **up/down 紅漲綠跌**，分數類走 **琥珀金**，兩者絕不混用——
否則「高分」會被讀成「上漲」。強度用 alpha 疊在深底上而非換色相。**飽和點跟著資料量級走**：
個股日漲跌 ±3%、產業中位數 ±1%、驗證淨報酬 ±1%；圖例標籤同步顯示該刻度，不得寫死。
**「無資料」畫成斜線紋**，與「中性」的極淡底色分開——分項 NULL 代表該成分不存在（權重已重分配），
不是「中性 50 分」。

treemap 的面積固定用成交值，不隨顏色維度改變：面積若跟著換，同一張圖在不同 metric 下
就不是同一個市場，無法比較。排版為自寫的 squarified treemap（`lib/treemap.ts`，無新增相依）。

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
