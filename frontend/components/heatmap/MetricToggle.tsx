"use client";

import {
  HEAT_METRIC_HELP,
  HEAT_METRIC_LABEL,
  heatGradient,
  NO_DATA_STYLE,
  RETURN_FULL,
  type HeatMetric,
} from "@/lib/heat";

/** 顏色維度切換。資料一次取齊三個 metric，切換不重打 API。 */
export function MetricToggle({
  value,
  onChange,
  options = ["return", "score", "inst"],
}: {
  value: HeatMetric;
  onChange: (m: HeatMetric) => void;
  options?: HeatMetric[];
}) {
  return (
    <div className="flex items-center gap-1 rounded-full border border-line-soft bg-panel-2/60 p-0.5">
      {options.map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => onChange(m)}
          title={HEAT_METRIC_HELP[m]}
          className={`rounded-full px-3 py-1 font-mono text-[11px] transition-colors ${
            value === m
              ? "bg-gold/15 text-gold"
              : "text-ink-faint hover:text-ink-dim"
          }`}
        >
          {HEAT_METRIC_LABEL[m]}
        </button>
      ))}
    </div>
  );
}

/** 色階圖例。報酬走紅綠雙向、分數走金色，兩者刻度語意不同，必須分開說明。
 *  `returnFull` 是報酬色階的飽和點——產業中位數與個股不同量級，標籤要跟著變，
 *  否則圖例會宣稱一個和實際著色不符的刻度。 */
export function HeatLegend({
  metric,
  returnFull = RETURN_FULL,
}: {
  metric: HeatMetric;
  returnFull?: number;
}) {
  const ends =
    metric === "return"
      ? [`-${(returnFull * 100).toFixed(1)}%`, `+${(returnFull * 100).toFixed(1)}%`]
      : ["0", "100"];

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 font-mono text-[10px] text-ink-faint">
      <span className="flex items-center gap-1.5">
        <span className="tnum">{ends[0]}</span>
        <span
          className="block h-3 w-24 rounded-[2px] border border-white/5"
          style={{ background: heatGradient(metric) }}
        />
        <span className="tnum">{ends[1]}</span>
      </span>
      <span className="flex items-center gap-1.5">
        <span
          className="block h-3 w-6 rounded-[2px] border border-white/5"
          style={NO_DATA_STYLE}
        />
        無資料
      </span>
      {metric === "return" && <span>紅漲 / 綠跌（台股語意）</span>}
    </div>
  );
}
