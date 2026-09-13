"use client";

/** 雙向分數區間滑桿：兩個原生 range 疊在同一條軌道上（保留鍵盤與輔助技術支援）。 */
export function ScoreRangeSlider({
  min,
  max,
  onChange,
  step = 5,
  lower = 0,
  upper = 100,
}: {
  min: number;
  max: number;
  onChange: (range: [number, number]) => void;
  step?: number;
  lower?: number;
  upper?: number;
}) {
  const pct = (v: number) => ((v - lower) / (upper - lower)) * 100;
  // 兩顆把手不可交錯；低點把手推到最右時需浮在上層，否則會被高點把手蓋住拖不回來
  const lowOnTop = min >= upper - step;

  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="font-mono text-[11px] tracking-wider text-ink-faint uppercase">
        分數區間
      </span>
      <div className="relative h-5 w-40">
        <div className="absolute top-1/2 h-1 w-full -translate-y-1/2 rounded-full bg-line" />
        <div
          className="absolute top-1/2 h-1 -translate-y-1/2 rounded-full bg-gold"
          style={{ left: `${pct(min)}%`, right: `${100 - pct(max)}%` }}
        />
        <input
          type="range"
          aria-label="最低分數"
          min={lower}
          max={upper}
          step={step}
          value={min}
          onChange={(e) => onChange([Math.min(Number(e.target.value), max), max])}
          className={`range-thumb absolute inset-0 w-full ${lowOnTop ? "z-20" : "z-10"}`}
        />
        <input
          type="range"
          aria-label="最高分數"
          min={lower}
          max={upper}
          step={step}
          value={max}
          onChange={(e) => onChange([min, Math.max(Number(e.target.value), min)])}
          className="range-thumb absolute inset-0 z-10 w-full"
        />
      </div>
      <span className="w-16 font-mono text-sm tnum text-gold">
        {min}–{max}
      </span>
    </div>
  );
}
