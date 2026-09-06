"use client";

import {
  BaselineSeries,
  createChart,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { Card, SectionTitle } from "@/components/Card";
import { api, type OrderFlowResponse } from "@/lib/api";
import { scoreColor } from "@/lib/format";

const UP = "#f0555c"; // 買/外盤/CVD 上 → 紅
const DOWN = "#24b981"; // 賣/內盤/CVD 下 → 綠

export function OrderFlowPanel({ symbol }: { symbol: string }) {
  const [d, setD] = useState<OrderFlowResponse | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .orderflow(symbol)
      .then((r) => !cancelled && setD(r))
      .catch(() => !cancelled && setErr(true));
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (err) return null;
  if (!d) {
    return (
      <Card>
        <SectionTitle>盤中資金流</SectionTitle>
        <div className="text-sm text-ink-faint">計算中…</div>
      </Card>
    );
  }
  if (d.trade_count === 0) {
    return (
      <Card>
        <SectionTitle>盤中資金流</SectionTitle>
        <div className="text-sm text-ink-faint">無當日逐筆，無法計算。</div>
      </Card>
    );
  }

  const buyPct = d.buy_ratio * 100;
  const sellPct = 100 - buyPct;
  const largeColor = d.large_delta > 0 ? "text-up" : d.large_delta < 0 ? "text-down" : "text-ink-dim";

  return (
    <Card>
      <div className="mb-5 flex items-center justify-between">
        <SectionTitle>盤中資金流（逐筆計算）</SectionTitle>
        <div className="flex items-baseline gap-2">
          <span className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
            盤中分項
          </span>
          <span className={`font-mono text-2xl font-bold tnum ${scoreColor(d.intraday_score)}`}>
            {d.intraday_score.toFixed(1)}
          </span>
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <div className="space-y-5">
          {/* 內外盤比 */}
          <div>
            <div className="mb-1.5 flex items-center justify-between text-sm">
              <span className="text-ink-dim">內外盤比（買/賣）</span>
              <span className="font-mono tnum">
                <span className="text-up">{buyPct.toFixed(0)}%</span>
                <span className="text-ink-faint"> / </span>
                <span className="text-down">{sellPct.toFixed(0)}%</span>
              </span>
            </div>
            <div className="flex h-2 overflow-hidden rounded-full bg-line-soft">
              <div className="bar-fill bg-up" style={{ width: `${buyPct}%` }} />
              <div className="bar-fill bg-down" style={{ width: `${sellPct}%` }} />
            </div>
            <div className="mt-1 flex justify-between font-mono text-[11px] text-ink-faint tnum">
              <span>外盤 {d.buy_volume.toLocaleString("zh-TW")}</span>
              <span>內盤 {d.sell_volume.toLocaleString("zh-TW")}</span>
            </div>
          </div>

          {/* 統計 */}
          <div className="grid grid-cols-2 gap-3">
            <Stat label="大單淨量" value={fmtSigned(d.large_delta)} color={largeColor} />
            <Stat
              label="CVD 量差"
              value={fmtSigned(d.cvd_final)}
              color={d.cvd_final > 0 ? "text-up" : d.cvd_final < 0 ? "text-down" : "text-ink-dim"}
            />
            <Stat label="大單買 / 賣" value={`${fmtK(d.large_buy)} / ${fmtK(d.large_sell)}`} />
            <Stat label="成交筆數" value={d.trade_count.toLocaleString("zh-TW")} />
          </div>
        </div>

        {/* CVD 走勢 */}
        <div>
          <div className="mb-1.5 text-sm text-ink-dim">CVD 累積量差（紅買綠賣）</div>
          <CvdChart series={d.cvd_series} />
        </div>
      </div>
    </Card>
  );
}

function CvdChart({ series }: { series: { t: number; cvd: number }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || series.length === 0) return;
    const chart = createChart(ref.current, {
      layout: {
        background: { color: "transparent" },
        textColor: "#9a968c",
        fontFamily: "var(--font-jbmono), monospace",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "rgba(38,38,46,0.4)" },
        horzLines: { color: "rgba(38,38,46,0.4)" },
      },
      rightPriceScale: { borderColor: "#26262e" },
      timeScale: {
        borderColor: "#26262e",
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: true,
        fixRightEdge: true,
      },
      localization: {
        timeFormatter: (t: unknown) =>
          new Date((t as number) * 1000).toISOString().slice(11, 16),
      },
      crosshair: { mode: 0 as const },
      autoSize: true,
    });
    const s = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: 0 },
      topLineColor: UP,
      topFillColor1: "rgba(240,85,92,0.25)",
      topFillColor2: "rgba(240,85,92,0.02)",
      bottomLineColor: DOWN,
      bottomFillColor1: "rgba(36,185,129,0.02)",
      bottomFillColor2: "rgba(36,185,129,0.25)",
      lineWidth: 2,
    });
    s.setData(series.map((p) => ({ time: p.t as UTCTimestamp, value: p.cvd })));
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.timeScale().fitContent());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [series]);
  return <div ref={ref} className="h-44 w-full" />;
}

function Stat({
  label,
  value,
  color = "text-ink",
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="rounded-xl border border-line-soft bg-panel-2/40 p-3">
      <div className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
        {label}
      </div>
      <div className={`mt-1 font-mono text-lg font-bold tnum ${color}`}>{value}</div>
    </div>
  );
}

function fmtSigned(v: number): string {
  const s = v > 0 ? "+" : "";
  return s + Math.round(v).toLocaleString("zh-TW");
}
function fmtK(v: number): string {
  return Math.round(v).toLocaleString("zh-TW");
}
