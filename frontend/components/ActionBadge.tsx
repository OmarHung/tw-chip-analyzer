import type { Action } from "@/lib/api";
import { ACTION_LABEL, ACTION_STYLE } from "@/lib/format";

export function ActionBadge({
  action,
  showZh = false,
}: {
  action: Action;
  showZh?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-mono text-[11px] font-bold tracking-wider ring-1 ring-inset ${ACTION_STYLE[action]}`}
    >
      {action}
      {showZh && <span className="font-sans font-medium">{ACTION_LABEL[action]}</span>}
    </span>
  );
}
