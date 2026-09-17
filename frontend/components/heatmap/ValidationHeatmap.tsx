"use client";

import { Fragment } from "react";
import { Card, SectionTitle } from "@/components/Card";
import type { ForwardHorizon } from "@/lib/api";
import { heatTextClass, returnColor } from "@/lib/heat";

/* 分數 bucket × horizon 的前瞻報酬矩陣——§28 成功標準的一眼版本。
   資料與下方各 horizon 表格同源（/api/validation/forward），這裡只是換一種讀法：
   若「分數越高 → 未來報酬越好」成立，每一列都應該由左到右漸紅。 */

/* 淨報酬的量級遠小於個股日漲跌（多在 ±1% 內），沿用 ±3% 的飽和點會讓整張圖一片灰。 */
const NET_FULL = 0.01;

export function ValidationHeatmap({ horizons }: { horizons: ForwardHorizon[] }) {
  const withData = horizons.filter((h) => h.buckets.length > 0);
  if (withData.length === 0) return null;
  const labels = withData[0].buckets.map((b) => b.label);

  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <SectionTitle>分數 × 持有期 報酬矩陣</SectionTitle>
        <span className="font-mono text-[10px] text-ink-faint">
          每格＝該 bucket 的平均淨報酬（含交易成本）
        </span>
      </div>

      <div className="overflow-x-auto">
        {/* 表頭與資料列共用一個 grid：橫軸 bucket 自動對齊，
            左側 horizon 欄用 auto（內容只有 1D~20D，寫死寬度會在左邊空一片）。 */}
        <div
          className="grid items-center gap-1"
          style={{
            minWidth: 36 + labels.length * 68,
            gridTemplateColumns: `auto repeat(${labels.length}, minmax(0, 1fr))`,
          }}
        >
          <div />
          {labels.map((l) => (
            <div
              key={l}
              className="pb-1 text-center font-mono text-[10px] text-ink-faint"
            >
              {l}
            </div>
          ))}

          {withData.map((h) => (
            <Fragment key={h.horizon}>
              <div className="pr-1 text-right font-mono text-[11px] text-ink-dim">
                {h.horizon}D
              </div>
              {h.buckets.map((b) => (
                <div
                  key={b.label}
                  className={`rounded-[3px] border border-white/5 px-1 py-2 text-center ${heatTextClass("return", b.avg_net, NET_FULL)}`}
                  style={{ background: returnColor(b.avg_net, NET_FULL) }}
                  title={`${h.horizon} 日｜${b.label}｜n=${b.n}｜勝率 ${
                    b.win_rate != null ? (b.win_rate * 100).toFixed(1) + "%" : "—"
                  }`}
                >
                  <div className="font-mono text-xs tnum">
                    {b.avg_net != null
                      ? `${b.avg_net > 0 ? "+" : ""}${(b.avg_net * 100).toFixed(2)}%`
                      : "—"}
                  </div>
                  {/* 樣本數是判讀可信度的關鍵，不能糊掉：跟著格子的文字色走，只降透明度 */}
                  <div className="font-mono text-[9px] opacity-75 tnum">
                    n={b.n.toLocaleString("zh-TW")}
                  </div>
                </div>
              ))}
            </Fragment>
          ))}
        </div>
      </div>

      <p className="mt-4 text-[11px] leading-relaxed text-ink-faint">
        若 Chip Score 有效，每一列應由左至右漸紅（分數越高、報酬越好）。
        目前各 horizon IC≈0、bucket 不單調——<span className="text-ink-dim">§28 尚未達成</span>，
        這張圖的用途是持續追蹤該現象何時出現，而非宣稱已出現。
      </p>
    </Card>
  );
}
