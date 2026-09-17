"use client";

import { Fragment, useEffect, useState } from "react";
import { Card, SectionTitle } from "@/components/Card";
import { api, type StockHeatCell, type StockHeatmapResponse } from "@/lib/api";
import { NO_DATA_STYLE, scoreHeatColor } from "@/lib/heat";
import { HeatLegend } from "./MetricToggle";

/* 個股籌碼分項 × 日期：看單一標的的籌碼結構怎麼演變（哪一維在轉強、哪一維長期缺資料）。

   全部欄位都是 0~100 分數，沒有漲跌維度，所以不給 metric 切換——
   硬加一個「漲跌」維度只會讓五列都變成同一個值。 */

const ROWS: { key: keyof StockHeatCell; label: string; help: string }[] = [
  { key: "chip_score", label: "總分", help: "Chip Score（當日全市場百分位）" },
  { key: "intraday", label: "盤中", help: "逐筆 order flow；當日無逐筆資料則無此成分" },
  { key: "institutional", label: "法人", help: "法人買賣超 + 信用 + 借券" },
  { key: "holder", label: "集保", help: "TDCC 股權分散；揭露落後數日，無快照則無此成分" },
  { key: "market", label: "大盤", help: "大盤 regime + 產業趨勢" },
];

const DATE_TICK_EVERY = 10;

export function StockScoreHeatmap({ symbol }: { symbol: string }) {
  const [data, setData] = useState<StockHeatmapResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .stockHeatmap(symbol)
      .then(setData)
      .catch(() => setError("分項熱力圖載入失敗"));
  }, [symbol]);

  const cells = data?.cells ?? [];

  return (
    <Card>
      <SectionTitle>籌碼分項演變</SectionTitle>

      {error ? (
        <Empty>{error}</Empty>
      ) : !data ? (
        <Empty>載入中…</Empty>
      ) : cells.length === 0 ? (
        <Empty>尚無已落地的每日分數（需盤後 EOD 累積）</Empty>
      ) : (
        <div className="overflow-x-auto">
          {/* 整張矩陣同一個 grid：列標籤欄用 auto（內容只有兩個字，寫死寬度會在左邊空一片），
              日期軸掛在同一個 grid 上，刻度自然對齊格子。 */}
          <div
            className="grid gap-y-px"
            style={{
              minWidth: 34 + cells.length * 10,
              gridTemplateColumns: `auto repeat(${cells.length}, minmax(0, 1fr))`,
              columnGap: 1,
            }}
          >
            {ROWS.map((row) => (
              <Fragment key={row.key}>
                <div
                  className="self-center pr-2 text-right text-[11px] text-ink-dim"
                  title={row.help}
                >
                  {row.label}
                </div>
                {cells.map((cell) => {
                  const raw = cell[row.key];
                  const v = typeof raw === "number" ? raw : null;
                  return (
                    <div
                      key={cell.date}
                      className={`rounded-[1px] ${row.key === "chip_score" ? "h-6" : "h-4"}`}
                      style={{
                        background: scoreHeatColor(v),
                        ...(v == null ? NO_DATA_STYLE : null),
                      }}
                      title={`${cell.date}｜${row.label} ${v?.toFixed(1) ?? "無資料"}${
                        cell.action ? `｜建議 ${cell.action}` : ""
                      }`}
                    />
                  );
                })}
              </Fragment>
            ))}

            <div />
            {cells.map((c, i) => (
              <div
                key={c.date}
                className="overflow-visible pt-1 font-mono text-[9px] whitespace-nowrap text-ink-faint"
              >
                {i % DATE_TICK_EVERY === 0 ? c.date.slice(5) : ""}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <HeatLegend metric="score" />
        <p className="text-right text-[10px] text-ink-faint">
          斜線＝當日沒有這個成分（分數由其餘成分重分配權重），不是「中性 50 分」
        </p>
      </div>
    </Card>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="py-10 text-center text-sm text-ink-faint">{children}</div>;
}
