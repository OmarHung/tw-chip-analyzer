"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
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
const LABEL_CH_W = 11; // text-[11px] 的中文一字約 11px
const LABEL_GAP = 8; // 標籤與第一格之間的呼吸

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

  // 標籤欄寬度依最長產業名而定。固定寬(舊做法 96px)在名稱短的日子會在左邊留一片空白，
  // 而它又必須是固定值——每列各自 fit-content 會讓列與列對不齊。
  const labelW = useMemo(
    () =>
      rows.reduce((w, r) => Math.max(w, r.industry.length * LABEL_CH_W), 0) +
      LABEL_GAP,
    [rows],
  );

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
          {/* 整張矩陣是同一個 grid（標籤欄 + 每個交易日一欄），列與列因此自動對齊 */}
          <div
            className="grid gap-y-px"
            style={{
              minWidth: labelW + data.dates.length * CELL_MIN_W,
              gridTemplateColumns: `${labelW}px repeat(${data.dates.length}, minmax(0, 1fr))`,
              columnGap: 1,
            }}
          >
            {rows.map((row) => (
              <Fragment key={row.industry}>
                <div
                  className="self-center truncate pr-2 text-right text-[11px] text-ink-dim"
                  title={`${row.industry}（視窗內最多 ${row.nMax} 檔）`}
                >
                  {row.industry}
                </div>
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
              </Fragment>
            ))}

            {/* 日期軸：每 5 格標一次，全標會擠成一團 */}
            <div />
            {data.dates.map((d, i) => (
              <div
                key={d}
                className="overflow-visible pt-1 font-mono text-[9px] whitespace-nowrap text-ink-faint"
              >
                {i % DATE_TICK_EVERY === 0 ? d.slice(5) : ""}
              </div>
            ))}
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
