// 型別化 API client，對應 FastAPI 後端（見 docs/05）。
//
// 瀏覽器一律用 NEXT_PUBLIC_API_BASE（build 時烤入，需為可從瀏覽器連到的位址）。
// SSR（server component，如總覽/個股頁）若設了 API_BASE_INTERNAL 則優先用它——
// 讓前端程序在後端直連 API（Docker 內走 http://api:8000），免繞公開網域/ts.net，
// 也避開容器解析不到 MagicDNS 的問題。非 NEXT_PUBLIC 變數不會外洩到 client bundle。
export const API_BASE =
  (typeof window === "undefined" && process.env.API_BASE_INTERNAL) ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://127.0.0.1:8099";

export type Action =
  | "BUY"
  | "WATCH"
  | "HOLD"
  | "REDUCE"
  | "EXIT"
  | "AVOID";

export interface Scores {
  intraday: number;
  institutional: number;
  holder: number;
  market: number;
}

export interface AnalysisResponse {
  symbol: string;
  name: string;
  market: string | null; // "TWSE"=上市 / "TPEx"=上櫃（興櫃未納入資料源）
  industry: string | null; // 產業別（MOPS 中文名，如「半導體」）
  price: number;
  change_pct: number | null;
  chip_score: number;
  scores: Scores;
  action: Action;
  entry: { low: number; high: number } | null;
  risk: {
    stop_loss: number | null;
    tp1: number | null;
    tp2: number | null;
    rr: number | null;
  };
  reasons: string[];
}

export interface ScannerRow {
  symbol: string;
  name: string;
  price: number;
  change_pct: number | null;
  chip_score: number;
  intraday: number;
  institutional: number;
  holder: number;
  action: Action;
  turnover: number;
  rr: number | null;
}

export interface ScannerResponse {
  as_of: string | null;
  count: number;
  rows: ScannerRow[];
}

export interface MarketInfo {
  taiex_close: number | null;
  taiex_ma20: number | null;
  taiex_ma60: number | null;
  advancers: number | null;
  decliners: number | null;
  trend_score: number | null;
  regime: string;
}

export interface DashboardResponse {
  as_of: string | null;
  total: number;
  action_counts: Record<string, number>;
  buy_candidates: number;
  watch_candidates: number;
  avg_chip_score: number;
  market: MarketInfo | null;
  top: {
    symbol: string;
    name: string;
    chip_score: number;
    change_pct: number | null;
    action: Action;
  }[];
}

export interface Bar {
  t: string | number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface ChartResponse {
  symbol: string;
  name: string;
  prev_close: number | null;
  daily: Bar[];
  intraday: Bar[];
}

export interface CorporateActionItem {
  date: string;
  kind: string; // 權 / 息 / 權息 / 面額 / 減資
  prev_close: number | null;
  reference_price: number | null;
  adj_factor: number | null; // 價格還原因子
  share_factor: number | null; // 量還原因子（1 舊股→幾新股）
}

/** feature_daily 的還原後價格特徵（chart 端點回的是未還原的原始價）。 */
export interface FeaturesResponse {
  symbol: string;
  name: string;
  date: string;
  close: number | null;
  raw_close: number | null;
  change_pct: number | null;
  ma20: number | null;
  atr14: number | null;
  vwap: number | null;
  recent_swing_low: number | null;
  close_vs_ma20_pct: number | null;
  close_vs_vwap_pct: number | null;
  actions: CorporateActionItem[];
}

export interface Tick {
  t: number; // epoch 秒（台北時間以 UTC 表示）
  time: string; // HH:MM:SS
  price: number;
  volume: number;
  side: number; // 1=買/外盤, -1=賣/內盤, 0=無法判定
  bid: number | null;
  ask: number | null;
}

export interface TicksResponse {
  symbol: string;
  date: string | null;
  count: number;
  ticks: Tick[];
}

export interface OrderFlowResponse {
  symbol: string;
  date: string | null;
  intraday_score: number;
  net_aggressor: number;
  large_net: number;
  buy_ratio: number;
  buy_volume: number;
  sell_volume: number;
  large_buy: number;
  large_sell: number;
  large_delta: number;
  cvd_final: number;
  trade_count: number;
  total_volume: number;
  cvd_series: { t: number; cvd: number }[];
}

export interface ScorePoint {
  t: string; // data_date YYYY-MM-DD
  chip_score: number;
  intraday: number | null;
  institutional: number | null;
  holder: number | null;
  market: number | null;
  action: Action | null;
}

export interface ScoreHistoryResponse {
  symbol: string;
  name: string;
  count: number;
  points: ScorePoint[];
}

export interface FlowPoint {
  t: string; // data_date YYYY-MM-DD
  close: number | null;
  foreign: number | null; // 外資（張，買超為正）
  trust: number | null; // 投信
  dealer: number | null; // 自營商（自行+避險）
  inst_total: number | null; // 三大法人合計
  margin_balance: number | null; // 融資餘額（張）
  short_balance: number | null; // 融券餘額（張）
}

export interface TdccSnapshot {
  date: string;
  retail_ratio: number | null;
  medium_ratio: number | null;
  large_ratio: number | null;
  super_large_ratio: number | null;
  holder_count: number | null;
}

export interface FlowSummary {
  foreign_5d: number;
  foreign_20d: number;
  foreign_60d: number;
  inst_5d: number;
  inst_20d: number;
  inst_60d: number;
  foreign_streak: number; // 外資連買(正)/連賣(負)天數
  margin_chg_20d: number; // 融資餘額近 20 交易日變化（張）
}

export type DivergenceStatus =
  | "bullish_div"
  | "bearish_div"
  | "aligned_up"
  | "aligned_down"
  | "neutral";

export interface DivergenceItem {
  window: number;
  price_return: number | null;
  inst_net: number; // 區間三大法人淨買超（張）
  inst_flow_ratio: number | null; // 淨買超佔區間成交比重
  price_inst_corr: number | null;
  status: DivergenceStatus;
  label: string;
  note: string;
}

export interface CostBasisPoint {
  t: string;
  cost: number | null; // 估算主力平均成本（元）
}

export type CostState = "profit" | "loss" | "flat" | "unknown";

export interface CostBasis {
  points: CostBasisPoint[];
  latest_cost: number | null;
  latest_price: number | null;
  premium_pct: number | null; // 現價/成本-1（正=浮盈）
  state: CostState;
  label: string;
}

export interface FlowsResponse {
  symbol: string;
  name: string;
  count: number;
  days: number;
  points: FlowPoint[];
  tdcc: TdccSnapshot | null;
  summary: FlowSummary | null;
  divergence: DivergenceItem[];
  cost_basis: CostBasis | null;
}

export interface DivergenceScanRow {
  symbol: string;
  name: string;
  price: number | null;
  change_pct: number | null;
  turnover: number;
  status: DivergenceStatus;
  label: string;
  window: number;
  price_return: number | null;
  flow_ratio: number | null; // 淨買超佔區間成交比重
  inst_net: number; // 區間三大法人淨買超（張）
  cost_state: CostState;
  premium_pct: number | null;
  mom_pct: number | null; // 60 日動能橫斷面百分位（研究訊號，未計入分數）
}

export interface DivergenceScanResponse {
  as_of: string | null;
  window: number;
  count: number;
  rows: DivergenceScanRow[];
}

export interface OpsQuota {
  available: boolean;
  bytes: number | null;
  limit_bytes: number | null;
  used_pct: number | null;
  cached_age_sec: number | null;
}

export interface OpsDateSpan {
  days: number;
  min: string | null;
  max: string | null;
  // 相對交易日曆(daily_price)缺幾日;非日頻資料源(TDCC/公司行動/逐筆)為 undefined。
  missing?: number;
  missing_recent?: string[];
}

export interface OpsTickDay {
  date: string;
  symbols: number;
  ticks: number;
}

export interface OpsCoverage {
  sources: Record<string, OpsDateSpan>;
  // 缺口比對的基準交易日曆(= daily_price 有資料的日)。
  calendar?: OpsDateSpan;
  row_counts: Record<string, number>;
  tick_by_date: OpsTickDay[];
  // 後端首次快取尚未就緒時為 true(涵蓋在背景計算中);就緒後為 false/undefined。
  loading?: boolean;
}

export interface OpsSchedule {
  enabled: boolean;
  running: boolean;
  timezone: string;
  eod: { day_of_week: string; hour: number; minute: number };
  next_run: string | null;
}

export interface OpsJob {
  state: "idle" | "running" | "done" | "error";
  kind: string | null;
  mode: string | null;
  target: string | null;
  step: string | null;
  progress: { done: number; total: number; [k: string]: number } | null;
  started_at: string | null;
  finished_at: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface OpsStatusResponse {
  quota: OpsQuota;
  coverage: OpsCoverage;
  schedule: OpsSchedule;
  job: OpsJob;
}

export interface ForwardBucket {
  label: string;
  n: number;
  win_rate: number | null;
  avg_net: number | null;
}

export interface ForwardHorizon {
  horizon: number;
  n: number;
  buckets: ForwardBucket[];
  ic: number | null;
  ic_t: number | null;
  ic_days: number;
}

export interface ForwardReport {
  as_of: string | null;
  total_signals: number;
  evaluated_latest_pending?: number;
  horizons: ForwardHorizon[];
}

export interface BackfillRequest {
  kind: "single" | "range";
  date?: string;
  mode?: "eod" | "ticks";
  start?: string;
  end?: string;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API ${path} 失敗：${res.status}`);
  }
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    // 後端以 {detail} 回錯誤訊息(FastAPI HTTPException)
    let detail = `${res.status}`;
    try {
      const j = await res.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* 忽略非 JSON 錯誤體 */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  dashboard: () => get<DashboardResponse>("/api/dashboard"),
  scanner: (params: Record<string, string | number | undefined> = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "") q.set(k, String(v));
    }
    const qs = q.toString();
    return get<ScannerResponse>(`/api/scanner${qs ? `?${qs}` : ""}`);
  },
  analysis: (symbol: string) =>
    get<AnalysisResponse>(`/api/stocks/${symbol}/analysis`),
  chart: (symbol: string) => get<ChartResponse>(`/api/stocks/${symbol}/chart`),
  features: (symbol: string, date?: string) =>
    get<FeaturesResponse>(
      `/api/stocks/${symbol}/features${date ? `?date=${date}` : ""}`,
    ),
  ticks: (symbol: string) => get<TicksResponse>(`/api/stocks/${symbol}/ticks`),
  scores: (symbol: string) =>
    get<ScoreHistoryResponse>(`/api/stocks/${symbol}/scores`),
  orderflow: (symbol: string) =>
    get<OrderFlowResponse>(`/api/stocks/${symbol}/orderflow`),
  flows: (symbol: string, days = 90) =>
    get<FlowsResponse>(`/api/stocks/${symbol}/flows?days=${days}`),
  divergenceScan: (
    params: { status?: string; min_turnover?: number; limit?: number } = {},
  ) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "") q.set(k, String(v));
    }
    const qs = q.toString();
    return get<DivergenceScanResponse>(
      `/api/scanner/divergence${qs ? `?${qs}` : ""}`,
    );
  },
  opsStatus: () => get<OpsStatusResponse>("/api/ops/status"),
  forwardReport: () => get<ForwardReport>("/api/validation/forward"),
  backfill: (body: BackfillRequest) =>
    post<OpsStatusResponse>("/api/ops/backfill", body),
};
