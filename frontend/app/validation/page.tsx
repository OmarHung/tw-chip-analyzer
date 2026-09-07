"use client";

import { useEffect, useState } from "react";
import { Card, SectionTitle } from "@/components/Card";
import { api, type ForwardHorizon, type ForwardReport } from "@/lib/api";

/* §28 成功標準的活體追蹤:分數 bucket vs 之後「實現」淨報酬。
   每天 EOD 落地新 snapshot,已實現樣本自然增加 → 這頁是持續累積的誠實 OOS。 */

function fmtPct(v: number | null): string {
  if (v == null) return "—";
  const p = v * 100;
  return `${p > 0 ? "+" : ""}${p.toFixed(2)}%`;
}

function netTone(v: number | null): string {
  if (v == null) return "text-ink-dim";
  return v > 0 ? "text-up" : v < 0 ? "text-down" : "text-ink-dim";
}

/* IC 顯著性:|t|>2 且 IC>0 → 分數有效(金);|t|>2 且 IC<0 → 反向(紅);其餘中性 */
function icTone(ic: number | null, t: number | null): string {
  if (ic == null || t == null || Math.abs(t) <= 2) return "text-ink-dim";
  return ic > 0 ? "text-gold-bright" : "text-up";
}

export default function ValidationPage() {
  const [data, setData] = useState<ForwardReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .forwardReport()
      .then(setData)
      .catch(() => setError("無法連線後端 API"));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-4xl leading-none">
          前瞻<span className="text-gold">驗證</span>
        </h1>
        <p className="mt-2 font-mono text-xs tracking-wider text-ink-faint">
          {data?.as_of ?? "—"} · 分數 vs 之後實現淨報酬(含交易成本) ·{" "}
          {data ? `${data.total_signals.toLocaleString("zh-TW")} 筆訊號` : "載入中"}
        </p>
      </div>

      {/* 成功標準說明 */}
      <div className="rounded-2xl border border-line-soft bg-panel-2/40 px-4 py-3 text-xs text-ink-dim sm:px-6">
        Phase 1 成功標準:<span className="text-ink">Chip Score 越高,未來報酬應有統計上單調改善(IC 顯著為正),且 MAE 不惡化</span>。
        本頁用每日落地的 signal_snapshot(預測先寫死)對照之後實現的報酬——零 look-ahead 的活體 OOS,
        樣本隨時間自動累積。目前 IC≈0 = 尚未達標(已知,詳見回測記錄);
        各 bucket 樣本數均衡是百分位映射的預期結果。
      </div>

      {error ? (
        <div className="rounded-2xl border border-line-soft bg-panel/70 p-10 text-center text-ink-dim">
          {error}
        </div>
      ) : !data ? (
        <div className="rounded-2xl border border-line-soft bg-panel/70 p-10 text-center text-ink-faint">
          計算中…(首次載入約數秒)
        </div>
      ) : (
        data.horizons.map((h) => <HorizonCard key={h.horizon} h={h} />)
      )}
    </div>
  );
}

function HorizonCard({ h }: { h: ForwardHorizon }) {
  const sig = h.ic != null && h.ic_t != null && Math.abs(h.ic_t) > 2;
  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <SectionTitle>持有 {h.horizon} 日</SectionTitle>
        <div className="flex items-baseline gap-4 font-mono text-xs tnum">
          <span className="text-ink-faint">
            n={h.n.toLocaleString("zh-TW")} · {h.ic_days} 日
          </span>
          <span className={icTone(h.ic, h.ic_t)}>
            IC {h.ic != null ? (h.ic > 0 ? "+" : "") + h.ic.toFixed(4) : "—"}
            {h.ic_t != null && ` (t=${h.ic_t})`}
            {sig && (h.ic! > 0 ? " ✓有效" : " ✗反向")}
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-line-soft font-mono text-[10px] tracking-[0.12em] text-ink-faint uppercase">
              <th className="py-2 pr-3 text-left font-medium">score bucket</th>
              <th className="px-3 py-2 text-right font-medium">n</th>
              <th className="px-3 py-2 text-right font-medium">win%</th>
              <th className="py-2 pl-3 text-right font-medium">avg net</th>
            </tr>
          </thead>
          <tbody>
            {h.buckets.map((b) => (
              <tr key={b.label} className="border-b border-line-soft/50 last:border-0">
                <td className="py-2 pr-3 font-mono text-sm text-ink">{b.label}</td>
                <td className="px-3 py-2 text-right font-mono text-sm tnum text-ink-dim">
                  {b.n.toLocaleString("zh-TW")}
                </td>
                <td className="px-3 py-2 text-right font-mono text-sm tnum text-ink-dim">
                  {b.win_rate != null ? `${(b.win_rate * 100).toFixed(1)}%` : "—"}
                </td>
                <td className={`py-2 pl-3 text-right font-mono text-sm tnum ${netTone(b.avg_net)}`}>
                  {fmtPct(b.avg_net)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
