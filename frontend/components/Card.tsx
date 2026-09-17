import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-2xl border border-line-soft bg-panel/70 p-6 shadow-[0_1px_0_rgba(255,255,255,0.03)_inset] ${className}`}
    >
      {children}
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="mb-5 font-mono text-[11px] tracking-[0.2em] text-ink-faint uppercase">
      {children}
    </h2>
  );
}

export function StatCard({
  label,
  value,
  accent = "text-ink",
  sub,
}: {
  label: string;
  value: ReactNode;
  accent?: string;
  sub?: ReactNode;
}) {
  return (
    <Card className="relative overflow-hidden">
      <div className="font-mono text-[11px] tracking-[0.15em] text-ink-faint uppercase">
        {label}
      </div>
      <div className={`mt-3 font-mono text-4xl font-bold tnum ${accent}`}>
        {value}
      </div>
      {sub && <div className="mt-1 text-xs text-ink-dim">{sub}</div>}
    </Card>
  );
}
