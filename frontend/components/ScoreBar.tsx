import { scoreBarColor, scoreColor } from "@/lib/format";

export function ScoreBar({
  label,
  score,
  hint,
  delay = 0,
}: {
  label: string;
  score: number;
  hint?: string;
  delay?: number;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-sm text-ink-dim">{label}</span>
        <span className={`font-mono text-sm font-bold tnum ${scoreColor(score)}`}>
          {score.toFixed(1)}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-line-soft">
        <div
          className={`bar-fill h-full rounded-full ${scoreBarColor(score)}`}
          style={{
            width: `${Math.max(0, Math.min(100, score))}%`,
            animationDelay: `${delay}ms`,
          }}
        />
      </div>
      {hint && <div className="mt-1 text-[11px] text-ink-faint">{hint}</div>}
    </div>
  );
}

/** 大型 chip score 環形視覺化。 */
export function ScoreRing({ score }: { score: number }) {
  const r = 42;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score)) / 100;
  const stroke =
    score >= 75 ? "var(--color-gold-bright)" : score >= 65 ? "var(--color-gold)" : score >= 50 ? "var(--color-ink-faint)" : "var(--color-line)";
  return (
    <div className="relative h-28 w-28">
      <svg className="h-full w-full -rotate-90" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r={r} fill="none" stroke="var(--color-line-soft)" strokeWidth="6" />
        <circle
          cx="50" cy="50" r={r} fill="none" stroke={stroke} strokeWidth="6"
          strokeLinecap="round" strokeDasharray={c}
          strokeDashoffset={c * (1 - pct)}
          style={{ transition: "stroke-dashoffset 1s cubic-bezier(0.22,1,0.36,1)" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={`font-mono text-3xl font-bold tnum ${scoreColor(score)}`}>
          {score.toFixed(1)}
        </span>
        <span className="font-mono text-[9px] tracking-[0.15em] text-ink-faint uppercase">
          Chip
        </span>
      </div>
    </div>
  );
}
