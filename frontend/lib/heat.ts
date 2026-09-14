/* 熱力圖色階。
 *
 * 兩條規則決定了這裡的所有選擇：
 * 1. 台股語意紅漲綠跌（與美股相反）——報酬類一律走 up/down，絕不借用金色。
 * 2. 分數類（chip_score、法人分項）走琥珀金，與價格紅綠分開，避免「高分」被讀成「上漲」。
 *
 * 強度用 alpha 疊在深炭黑底上，而非換色相：同一色相的深淺在深色介面上比多色相更好讀，
 * 也讓「沒有資料」（完全無底色 + 斜線）與「中性」（極淡底色）不會混淆。
 */

import type { CSSProperties } from "react";

export type HeatMetric = "return" | "score" | "inst";

export const HEAT_METRIC_LABEL: Record<HeatMetric, string> = {
  return: "漲跌幅",
  score: "籌碼分數",
  inst: "法人強度",
};

export const HEAT_METRIC_HELP: Record<HeatMetric, string> = {
  return: "當日漲跌幅（產業列為成分股中位數）。紅漲綠跌。",
  score: "Chip Score 0~100（當日全市場百分位）。50 為中性，金色越亮越強。",
  inst: "法人分項 0~100。僅反映法人買賣超強度，不等於整體籌碼分數。",
};

/* 報酬色階的飽和點：±3%。台股單日漲跌幅上限 10%，但多數日子落在 ±3% 內，
   用 10% 當飽和會讓整張圖淡到看不出差異。超過 3% 的一律吃滿色。 */
export const RETURN_FULL = 0.03;
/* 產業列是成分股的中位數，波動遠小於個股（多在 ±1% 內）——沿用個股的 ±3%
   會讓整張產業矩陣淡到分不出強弱。飽和點必須跟著資料的量級走。 */
export const MEDIAN_RETURN_FULL = 0.01;
/* 分數色階以 50 為中點，±50 到底。 */
const SCORE_MID = 50;

const UP = "240, 85, 92"; /* --color-up  漲 */
const DOWN = "36, 185, 129"; /* --color-down 跌 */
const GOLD = "224, 176, 99"; /* --color-gold 分數 */
const COLD = "99, 97, 91"; /* --color-ink-faint 低分側，刻意不用綠：那是「跌」的意思 */

const MIN_ALPHA = 0.06; /* 接近中性時仍留一點底色，格子才不會看起來像缺資料 */
const MAX_ALPHA = 0.88;

function alpha(ratio: number): number {
  const clamped = Math.min(Math.abs(ratio), 1);
  return MIN_ALPHA + (MAX_ALPHA - MIN_ALPHA) * clamped;
}

/** 漲跌幅 → 紅綠。`full` 可調飽和點（如驗證頁的淨報酬量級較小）。 */
export function returnColor(pct: number | null | undefined, full = RETURN_FULL): string {
  if (pct == null) return "transparent";
  if (pct === 0) return `rgba(${COLD}, ${MIN_ALPHA})`;
  return `rgba(${pct > 0 ? UP : DOWN}, ${alpha(pct / full)})`;
}

/** 0~100 分數 → 金（高）／冷灰（低），50 為中點。 */
export function scoreHeatColor(score: number | null | undefined): string {
  if (score == null) return "transparent";
  const d = (score - SCORE_MID) / SCORE_MID;
  return `rgba(${d >= 0 ? GOLD : COLD}, ${alpha(d)})`;
}

export function heatColor(
  metric: HeatMetric,
  value: number | null | undefined,
  returnFull = RETURN_FULL,
): string {
  return metric === "return" ? returnColor(value, returnFull) : scoreHeatColor(value);
}

/** 圖例用的連續色條。端點即飽和色，與格子用的是同一組函式，不會走鐘。 */
export function heatGradient(metric: HeatMetric): string {
  const stops =
    metric === "return"
      ? [returnColor(-1, 1), returnColor(0), returnColor(1, 1)]
      : [scoreHeatColor(0), scoreHeatColor(SCORE_MID), scoreHeatColor(100)];
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

/** 格子文字色。
 *
 * 底色濃時要換**深**字而不是更亮的字：alpha 接近 0.9 的琥珀金已是淺色背景，
 * 疊上淺色文字等於白底白字。淡底（深炭黑為主）才用淺字。 */
export function heatTextClass(
  metric: HeatMetric,
  value: number | null | undefined,
  returnFull = RETURN_FULL,
): string {
  if (value == null) return "text-ink-faint";
  const strong =
    metric === "return"
      ? Math.abs(value) >= returnFull * 0.55
      : Math.abs(value - SCORE_MID) >= 22;
  return strong ? "text-bg" : "text-ink";
}

export function formatHeatValue(
  metric: HeatMetric,
  value: number | null | undefined,
): string {
  if (value == null) return "—";
  if (metric === "return") {
    const p = value * 100;
    return `${p > 0 ? "+" : ""}${p.toFixed(2)}%`;
  }
  return value.toFixed(1);
}

/** 「無資料」格：斜線紋，與「中性」的淡底色在視覺上分得開。 */
export const NO_DATA_STYLE: CSSProperties = {
  backgroundImage:
    "repeating-linear-gradient(45deg, rgba(255,255,255,0.045) 0 1px, transparent 1px 5px)",
};
