"use client";

import { useState } from "react";
import { Card, SectionTitle } from "@/components/Card";
import type { OpsCoverage, OpsDateSpan } from "@/lib/api";

const SOURCE_ZH: Record<string, { zh: string; en: string }> = {
  feature_daily: { zh: "特徵", en: "feature_daily" },
  daily_price: { zh: "日K", en: "daily_price" },
  institutional_daily: { zh: "法人", en: "institutional" },
  margin_daily: { zh: "融資券", en: "margin" },
  tdcc_summary_weekly: { zh: "集保", en: "TDCC" },
  sbl_daily: { zh: "借券", en: "SBL" },
  market_daily: { zh: "大盤", en: "market_daily" },
  corporate_action: { zh: "除權息", en: "corporate_action" },
  raw_tick: { zh: "逐筆", en: "raw_tick" },
};

/** 所有資料源缺漏日的聯集（與後端 kind="missing" 補的是同一組日子）。 */
function missingUnion(coverage: OpsCoverage): string[] {
  const all = new Set<string>();
  for (const span of Object.values(coverage.sources)) {
    for (const d of span.missing_dates ?? []) all.add(d);
  }
  return [...all].sort();
}

export function CoverageCard({
  coverage,
  onFillMissing,
  disabled,
}: {
  coverage: OpsCoverage;
  onFillMissing: () => void;
  disabled: boolean;
}) {
  // 展開中的資料源（點缺口數字切換），null = 全部收合
  const [openKey, setOpenKey] = useState<string | null>(null);
  const union = missingUnion(coverage);

  return (
    <Card>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <SectionTitle>資料源涵蓋</SectionTitle>
        {union.length > 0 && (
          <button
            type="button"
            onClick={onFillMissing}
            disabled={disabled}
            className="rounded-lg border border-gold/40 bg-gold/10 px-3 py-1.5 font-mono text-[11px] tracking-wider text-gold transition-colors hover:bg-gold/20 disabled:cursor-not-allowed disabled:opacity-40"
          >
            補齊缺漏 {union.length} 日
          </button>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-line-soft font-mono text-[10px] tracking-[0.12em] whitespace-nowrap text-ink-faint uppercase">
              <th className="py-2 pr-3 text-left font-medium">資料源</th>
              <th className="px-3 py-2 text-right font-medium">交易日</th>
              <th className="px-3 py-2 text-right font-medium">缺口</th>
              <th className="px-3 py-2 text-right font-medium">最早</th>
              <th className="py-2 pl-3 text-right font-medium">最新</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(coverage.sources).map(([k, v]) => (
              <CoverageRow
                key={k}
                sourceKey={k}
                span={v}
                open={openKey === k}
                onToggle={() => setOpenKey(openKey === k ? null : k)}
              />
            ))}
          </tbody>
        </table>
      </div>

      <p className="pt-3 text-xs text-ink-faint">
        缺口 = 該資料源「起始日之後」少掉的交易日（相對已知交易日曆＝日K 有資料的
        {coverage.calendar?.days ?? 0} 日）。點缺口數字看是哪幾天。特徵/大盤前段沒有
        資料不算缺口——那是回看視窗（20 日 / MA60）本來就填不出值，標成「晚 N 日起」。
        集保（週度）、除權息（事件表）、逐筆（受 Shioaji 配額限制）不適用，顯示「—」。
        整條資料鏈都沒補的日子不會出現在日曆裡，故此欄看不出「整天全缺」——那要看最新
        日期是否落後今天。
      </p>
    </Card>
  );
}

function CoverageRow({
  sourceKey,
  span,
  open,
  onToggle,
}: {
  sourceKey: string;
  span: OpsDateSpan;
  open: boolean;
  onToggle: () => void;
}) {
  const label = SOURCE_ZH[sourceKey];
  const dates = span.missing_dates ?? [];
  const hasGap = (span.missing ?? 0) > 0;

  return (
    <>
      <tr className="border-b border-line-soft/50 last:border-0">
        <td className="py-2.5 pr-3 text-sm whitespace-nowrap text-ink">
          {label?.zh ?? sourceKey}
          {label?.en && (
            <span className="ml-1.5 hidden font-mono text-[11px] text-ink-faint sm:inline">
              {label.en}
            </span>
          )}
        </td>
        <td className="px-3 py-2.5 text-right font-mono text-sm tnum text-ink">
          {span.days}
        </td>
        <td className="px-3 py-2.5 text-right font-mono text-sm tnum">
          {span.missing === undefined ? (
            <span className="text-ink-faint">—</span>
          ) : hasGap ? (
            <button
              type="button"
              onClick={onToggle}
              aria-expanded={open}
              className="text-gold underline decoration-dotted underline-offset-4 hover:decoration-solid"
            >
              缺 {span.missing}
            </button>
          ) : (
            <span className="text-ink-faint">齊</span>
          )}
        </td>
        <td className="px-3 py-2.5 text-right font-mono text-sm whitespace-nowrap tnum text-ink-dim">
          {span.min ?? "—"}
          {/* 起始落差：不是缺漏，是這個源本來就晚開始（回看視窗填不出值） */}
          {(span.starts_late ?? 0) > 0 && (
            <span className="ml-1.5 text-[10px] text-ink-faint">
              晚 {span.starts_late} 日起
            </span>
          )}
        </td>
        <td className="py-2.5 pl-3 text-right font-mono text-sm whitespace-nowrap tnum text-ink-dim">
          {span.max ?? "—"}
        </td>
      </tr>

      {open && dates.length > 0 && (
        <tr className="border-b border-line-soft/50">
          <td colSpan={5} className="pb-3">
            <div className="rounded-lg border border-line-soft bg-panel-2/50 p-3">
              <div className="mb-2 font-mono text-[10px] tracking-wider text-ink-faint uppercase">
                缺漏日期（{dates.length}）
              </div>
              <div className="flex flex-wrap gap-1.5">
                {dates.map((d) => (
                  <span
                    key={d}
                    className="rounded border border-line-soft px-1.5 py-0.5 font-mono text-[11px] tnum text-ink-dim"
                  >
                    {d}
                  </span>
                ))}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
