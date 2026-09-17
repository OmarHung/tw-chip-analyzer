import type { Action } from "@/lib/api";
import { ACTION_LABEL, ACTION_STYLE } from "@/lib/format";

export function ActionBadge({
  action,
  showZh = false,
  shortOnMobile = false,
}: {
  action: Action;
  showZh?: boolean;
  /** 窄螢幕縮成首字母（B/W/H/R/E/A，彼此不重複）。用於列表這種每列都要塞進代號、
   *  名稱、漲跌、分數的地方；結論與篩選器不該開，那裡讀得懂比省空間重要。 */
  shortOnMobile?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md py-0.5 font-mono text-[11px] font-bold tracking-wider ring-1 ring-inset ${
        shortOnMobile ? "px-1.5 sm:px-2" : "px-2"
      } ${ACTION_STYLE[action]}`}
    >
      {shortOnMobile ? (
        <>
          <span className="sm:hidden">{action[0]}</span>
          <span className="hidden sm:inline">{action}</span>
        </>
      ) : (
        action
      )}
      {showZh && <span className="font-sans font-medium">{ACTION_LABEL[action]}</span>}
    </span>
  );
}
