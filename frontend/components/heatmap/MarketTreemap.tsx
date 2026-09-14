"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Card, SectionTitle } from "@/components/Card";
import { api, type MarketHeatCell, type MarketHeatmapResponse } from "@/lib/api";
import { fmtTurnover } from "@/lib/format";
import {
  formatHeatValue,
  heatColor,
  heatTextClass,
  NO_DATA_STYLE,
  type HeatMetric,
} from "@/lib/heat";
import { groupedTreemap } from "@/lib/treemap";
import { HeatLegend, MetricToggle } from "./MetricToggle";

/* 全市場 treemap：方塊面積＝成交值，顏色＝選定的維度。
   面積固定用成交值（而非隨 metric 改變）——面積若跟著顏色一起換，
   同一張圖在不同 metric 下就不是同一個市場，無法比較。 */

/* 上緣留較多空間給產業名：等距內縮會讓第一個葉方塊直接蓋在標題上，
   標題和代號疊在一起兩個都讀不到。 */
const GROUP_PADDING = { top: 13, right: 3, bottom: 3, left: 3 };
const HEIGHT = 560;
const MIN_LABEL_W = 42; // 小於這個寬高就只剩色塊，硬塞文字會糊成一片
const MIN_LABEL_H = 24;
const MIN_NAME_W = 76; // 代號與股名同列所需寬度，不夠就只留代號
const MIN_VALUE_H = 34; // 再矮就只放代號那一行
const LARGE_CELL = { w: 120, h: 80 }; // 大方塊放大字級，權值股一眼可辨

function cellValue(cell: MarketHeatCell, metric: HeatMetric): number | null {
  if (metric === "return") return cell.change_pct;
  if (metric === "score") return cell.chip_score;
  return cell.institutional;
}

export function MarketTreemap() {
  const [data, setData] = useState<MarketHeatmapResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [metric, setMetric] = useState<HeatMetric>("return");
  const [width, setWidth] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .marketHeatmap()
      .then(setData)
      .catch(() => setError("熱力圖載入失敗"));
  }, []);

  // treemap 需要實際像素才能排版，容器寬度變了就重算（RWD / 側欄開合）
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      setWidth(entry.contentRect.width);
    });
    observer.observe(el);
    setWidth(el.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, []);

  const layout = useMemo(() => {
    if (!data || width <= 0) return null;
    const items = data.rows.map((r) => ({ ...r, value: r.turnover }));
    return groupedTreemap(
      items,
      { x: 0, y: 0, w: width, h: HEIGHT },
      (r) => r.industry ?? "其他",
      GROUP_PADDING,
    );
  }, [data, width]);

  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <SectionTitle>市場熱力圖</SectionTitle>
        <MetricToggle value={metric} onChange={setMetric} />
      </div>

      <div ref={boxRef} className="relative w-full" style={{ height: HEIGHT }}>
        {error ? (
          <Centered>{error}</Centered>
        ) : !data ? (
          <Centered>載入中…</Centered>
        ) : data.rows.length === 0 ? (
          <Centered>尚無當日資料</Centered>
        ) : (
          layout && (
            <>
              {layout.groups.map((g) => (
                <div
                  key={g.key}
                  className="pointer-events-none absolute rounded-[3px] border border-line/70"
                  style={{ left: g.x, top: g.y, width: g.w, height: g.h }}
                >
                  {g.w > 56 && g.h > 30 && (
                    <span className="absolute left-1 top-0 max-w-full truncate text-[10px] leading-[13px] text-ink-dim">
                      {g.key}
                    </span>
                  )}
                </div>
              ))}
              {layout.leaves.map((cell) => {
                const v = cellValue(cell, metric);
                return (
                  <Link
                    key={cell.symbol}
                    href={`/stocks/${cell.symbol}`}
                    title={`${cell.symbol} ${cell.name}｜${cell.industry ?? "未分類"}
成交值 ${fmtTurnover(cell.turnover)}｜漲跌 ${formatHeatValue("return", cell.change_pct)}
分數 ${cell.chip_score.toFixed(1)}｜法人 ${cell.institutional?.toFixed(1) ?? "—"}｜${cell.action}`}
                    className="absolute flex items-center justify-center overflow-hidden rounded-[2px] border border-black/30 transition-[filter] hover:brightness-125"
                    style={{
                      left: cell.x,
                      top: cell.y,
                      width: Math.max(cell.w - 1, 0),
                      height: Math.max(cell.h - 1, 0),
                      background: heatColor(metric, v),
                      ...(v == null ? NO_DATA_STYLE : null),
                    }}
                  >
                    {cell.w > MIN_LABEL_W && cell.h > MIN_LABEL_H && (
                      <CellLabel cell={cell} metric={metric} value={v} />
                    )}
                  </Link>
                );
              })}
            </>
          )
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <HeatLegend metric={metric} />
        {data && data.rows.length > 0 && (
          <p className="font-mono text-[10px] text-ink-faint tnum">
            方塊面積＝成交值 · 前 {data.count}/{data.total} 檔 · 涵蓋當日成交值{" "}
            {(data.covered_turnover * 100).toFixed(1)}%
          </p>
        )}
      </div>
    </Card>
  );
}

type CellLabelProps = {
  cell: MarketHeatCell & { w: number; h: number };
  metric: HeatMetric;
  value: number | null;
};

/* 方塊越大放越多：代號 →（同列）股名 → 數值。
   所有文字同色：濃底上必須整組翻成深字，只翻一行會有一行讀不到。 */
function CellLabel({ cell, metric, value }: CellLabelProps) {
  const isLarge = cell.w >= LARGE_CELL.w && cell.h >= LARGE_CELL.h;
  const showName = cell.w >= MIN_NAME_W;
  const showValue = cell.h >= MIN_VALUE_H;

  return (
    // 方塊本身是 flex 置中容器（見 Link）；這裡撐滿寬度才能讓 truncate 正常裁切。
    <span
      className={`flex w-full flex-col items-center gap-0.5 px-1 text-center ${heatTextClass(metric, value)}`}
    >
      <span className="flex w-full min-w-0 items-baseline justify-center gap-1">
        {/* 代號不裁切：它是辨識主鍵，寧可擠掉股名 */}
        <span
          className={`shrink-0 font-mono leading-tight ${isLarge ? "text-[12px]" : "text-[10px]"}`}
        >
          {cell.symbol}
        </span>
        {showName && (
          <span
            className={`truncate font-sans leading-tight ${isLarge ? "text-[12px]" : "text-[10px]"}`}
          >
            {cell.name}
          </span>
        )}
      </span>
      {showValue && (
        <span
          className={`block truncate font-mono leading-tight opacity-80 tnum ${isLarge ? "text-[11px]" : "text-[9px]"}`}
        >
          {formatHeatValue(metric, value)}
        </span>
      )}
    </span>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center text-sm text-ink-faint">
      {children}
    </div>
  );
}
