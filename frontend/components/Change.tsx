import { changeArrow, dirColor, fmtChange } from "@/lib/format";

/** 漲跌幅顯示（台股：漲紅跌綠）。 */
export function Change({
  pct,
  className = "",
}: {
  pct: number | null | undefined;
  className?: string;
}) {
  return (
    <span className={`font-mono tnum ${dirColor(pct)} ${className}`}>
      {pct != null && pct !== 0 && (
        <span className="mr-0.5 text-[0.7em]">{changeArrow(pct)}</span>
      )}
      {fmtChange(pct)}
    </span>
  );
}
