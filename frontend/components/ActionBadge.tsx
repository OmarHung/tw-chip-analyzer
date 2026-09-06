import type { Action } from "@/lib/api";
import { ACTION_STYLE } from "@/lib/format";

export function ActionBadge({ action }: { action: Action }) {
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${ACTION_STYLE[action]}`}
    >
      {action}
    </span>
  );
}
