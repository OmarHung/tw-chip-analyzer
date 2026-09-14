"use client";

import { useEffect, useMemo, useState } from "react";
import { Card, SectionTitle } from "@/components/Card";
import {
  api,
  type IndustryHeatCell,
  type IndustryHeatmapResponse,
} from "@/lib/api";
import {
  formatHeatValue,
  heatColor,
  MEDIAN_RETURN_FULL,
  NO_DATA_STYLE,
  type HeatMetric,
} from "@/lib/heat";
import { HeatLegend, MetricToggle } from "./MetricToggle";

/* 產業輪動矩陣：橫軸交易日、縱軸產業，每格是該產業成分股的中位數。
   列的排序由後端決定（依最新一日報酬），所以最上面幾列就是今天在動的產業。 */

const CELL_MIN_W = 16; // 格子再窄就點不到也讀不出來；窄於容器時改為橫向捲動
const DATE_TICK_EVERY = 5;

function cellValue(cell: IndustryHeatCell | undefined, metric: HeatMetric): number | null {
  if (!cell) return null;
  if (metric === "return") return cell.ret;
  if (metric === "score") return cell.score;
  return cell.inst;
}

export function IndustryHeatmap() {
  const [data, setData] = useState<IndustryHeatmapResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [metric, setMetric] = useState<HeatMetric>("return");

  useEffect(() => {
    api
      .industryHeatmap()
      .then(setData)
      .catch(() => setError("產業熱力圖載入失敗"));
  }, []);

  // 每列先攤成「與 dates 等長」的陣列，缺漏日留 undefined → 畫成無資料而非往前擠
  const rows = useMemo(() => {
    if (!data) return [];
    return data.industries.map((row) => {
      const byDate = new Map(row.cells.map((c) => [c.date, c]));
      return {
        industry: row.industry,
        nMax: row.n_max,
        cells: data.dates.map((d) => byDate.get(d)),
      };
    });
  }, [data]);

  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <SectionTitle>產業輪動</SectionTitle>
        <MetricToggle value={metric} onChange={setMetric} />
      </div>

      {error ? (
        <Empty>{error}</Empty>
      ) : !data ? (
        <Empty>載入中…</Empty>
      ) : rows.length === 0 ? (
        <Empty>尚無足夠成分股的產業資料</Empty>
      ) : (
        <div className="overflow-x-auto">
          <div style={{ minWidth: 120 + data.dates.length * CELL_MIN_W }}>
            {rows.map((row) => (
              <div key={row.industry} className="flex items-center gap-2 py-px">
                <div
                  className="shrink-0 truncate text-right text-[11px] text-ink-dim"
                  style={{ width: 96 }}
                  title={`${row.industry}（視窗內最多 ${row.nMax} 檔）`}
                >
                  {row.industry}
                </div>
                <div
                  className="grid flex-1 gap-px"
                  style={{
                    gridTemplateColumns: `repeat(${data.dates.length}, minmax(0, 1fr))`,
                  }}
                >
                  {row.cells.map((cell, i) => {
                    const v = cellValue(cell, metric);
                    return (
                      <div
                        key={data.dates[i]}
                        className="h-5 rounded-[1px]"
                        style={{
                          background: heatColor(metric, v, MEDIAN_RETURN_FULL),
                          ...(v == null ? NO_DATA_STYLE : null),
                        }}
                        title={
                          cell
                            ? `${row.industry}｜${cell.date}｜${cell.n} 檔
漲跌中位數 ${formatHeatValue("return", cell.ret)}｜分數 ${cell.score?.toFixed(1) ?? "—"}｜法人 ${cell.inst?.toFixed(1) ?? "—"}`
                            : `${row.industry}｜${data.dates[i]}｜無資料`
                        }
                      />
                    );
                  })}
                </div>
              </div>
            ))}

            {/* 日期軸：每 5 格標一次，全標會擠成一團 */}
            <div className="flex items-center gap-2 pt-1">
              <div className="shrink-0" style={{ width: 96 }} />
              <div
                className="grid flex-1 gap-px"
                style={{
                  gridTemplateColumns: `repeat(${data.dates.length}, minmax(0, 1fr))`,
                }}
              >
                {data.dates.map((d, i) => (
                  <div
                    key={d}
                    className="overflow-visible font-mono text-[9px] whitespace-nowrap text-ink-faint"
                  >
                    {i % DATE_TICK_EVERY === 0 ? d.slice(5) : ""}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <HeatLegend metric={metric} returnFull={MEDIAN_RETURN_FULL} />
        {data && (
          <p className="max-w-md text-right font-mono text-[10px] text-ink-faint">
            每格＝該產業當日成分股中位數 · 成分股 &lt; {data.min_symbols} 檔的產業不列入
          </p>
        )}
      </div>
    </Card>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="py-10 text-center text-sm text-ink-faint">{children}</div>
  );
}
