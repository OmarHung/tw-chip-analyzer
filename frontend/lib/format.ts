import type { Action } from "./api";

/* 台股語意：紅漲(up) / 綠跌(down)。動作/分數用金色系，與價格紅綠區隔。 */

export const ACTION_STYLE: Record<Action, string> = {
  BUY: "bg-gold/15 text-gold-bright ring-gold/40",
  WATCH: "bg-gold/10 text-gold ring-gold/25",
  HOLD: "bg-white/5 text-ink-dim ring-white/10",
  REDUCE: "bg-up/10 text-up ring-up/25",
  EXIT: "bg-up/15 text-up ring-up/30",
  AVOID: "bg-white/5 text-ink-faint ring-white/5",
};

export const ACTION_LABEL: Record<Action, string> = {
  BUY: "買進",
  WATCH: "觀察",
  HOLD: "持有",
  REDUCE: "減碼",
  EXIT: "出場",
  AVOID: "避開",
};

/* Chip score → 金色強度 */
export function scoreColor(score: number): string {
  if (score >= 75) return "text-gold-bright";
  if (score >= 65) return "text-gold";
  if (score >= 50) return "text-ink-dim";
  return "text-ink-faint";
}

export function scoreBarColor(score: number): string {
  if (score >= 75) return "bg-gold-bright";
  if (score >= 65) return "bg-gold";
  if (score >= 50) return "bg-ink-faint";
  return "bg-line";
}

/* 漲跌方向色（台股：漲紅跌綠） */
export function dirColor(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-ink-dim";
  return v > 0 ? "text-up" : "text-down";
}

export function fmtChange(pct: number | null | undefined): string {
  if (pct == null) return "—";
  const p = pct * 100;
  const sign = p > 0 ? "+" : "";
  return `${sign}${p.toFixed(2)}%`;
}

export function changeArrow(v: number | null | undefined): string {
  if (v == null || v === 0) return "";
  return v > 0 ? "▲" : "▼";
}

export function fmtTurnover(v: number): string {
  if (v >= 1e8) return `${(v / 1e8).toFixed(1)} 億`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(0)} 萬`;
  return v.toFixed(0);
}

export function fmtPrice(v: number): string {
  return v.toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}
