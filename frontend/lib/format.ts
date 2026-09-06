import type { Action } from "./api";

export const ACTION_STYLE: Record<Action, string> = {
  BUY: "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30",
  WATCH: "bg-amber-500/15 text-amber-400 ring-amber-500/30",
  HOLD: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
  REDUCE: "bg-orange-500/15 text-orange-400 ring-orange-500/30",
  EXIT: "bg-rose-500/15 text-rose-400 ring-rose-500/30",
  AVOID: "bg-rose-500/15 text-rose-400 ring-rose-500/30",
};

export function scoreColor(score: number): string {
  if (score >= 75) return "text-emerald-400";
  if (score >= 65) return "text-amber-400";
  if (score >= 50) return "text-slate-300";
  return "text-rose-400";
}

export function scoreBarColor(score: number): string {
  if (score >= 75) return "bg-emerald-500";
  if (score >= 65) return "bg-amber-500";
  if (score >= 50) return "bg-slate-500";
  return "bg-rose-500";
}

export function fmtTurnover(v: number): string {
  if (v >= 1e8) return `${(v / 1e8).toFixed(1)} 億`;
  if (v >= 1e4) return `${(v / 1e4).toFixed(0)} 萬`;
  return v.toFixed(0);
}

export function fmtPrice(v: number): string {
  return v.toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}
