// 型別化 API client，對應 FastAPI 後端（見 docs/05）。

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8099";

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

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API ${path} 失敗：${res.status}`);
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
  ticks: (symbol: string) => get<TicksResponse>(`/api/stocks/${symbol}/ticks`),
  scores: (symbol: string) =>
    get<ScoreHistoryResponse>(`/api/stocks/${symbol}/scores`),
  orderflow: (symbol: string) =>
    get<OrderFlowResponse>(`/api/stocks/${symbol}/orderflow`),
};
