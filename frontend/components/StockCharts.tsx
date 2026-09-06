"use client";

import {
  AreaSeries,
  CandlestickSeries,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { api, type Bar, type ChartResponse } from "@/lib/api";
import { SectionTitle } from "@/components/Card";
import { dirColor } from "@/lib/format";

const UP = "#f0555c"; // 漲/紅
const DOWN = "#24b981"; // 跌/綠

const CHART_OPTS = {
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
  timeScale: { borderColor: "#26262e" },
  crosshair: { mode: 0 as const },
  autoSize: true,
};

type Tab = "daily" | "intraday";

export function StockCharts({ symbol }: { symbol: string }) {
  const [data, setData] = useState<ChartResponse | null>(null);
  const [tab, setTab] = useState<Tab>("daily");
  const [err, setErr] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .chart(symbol)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setErr(true));
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (err) {
    return <Empty msg="圖表資料暫時無法取得。" />;
  }
  if (!data) {
    return <Empty msg="載入圖表中…" />;
  }

  const hasIntraday = data.intraday.length > 0;

  return (
    <div className="space-y-6">
      <div>
        <div className="mb-4 flex items-center justify-between">
          <SectionTitle>價格走勢</SectionTitle>
          <div className="flex gap-1 rounded-lg border border-line-soft bg-panel-2/50 p-0.5">
            <TabBtn active={tab === "daily"} onClick={() => setTab("daily")}>
              日K線
            </TabBtn>
            <TabBtn
              active={tab === "intraday"}
              onClick={() => setTab("intraday")}
              disabled={!hasIntraday}
            >
              分時
            </TabBtn>
          </div>
        </div>
        {tab === "daily" ? (
          <CandleChart bars={data.daily} />
        ) : (
          <AreaChart bars={data.intraday} prevClose={data.prev_close} />
        )}
      </div>

      <TradeTable bars={data.intraday} prevClose={data.prev_close} />
    </div>
  );
}

function CandleChart({ bars }: { bars: Bar[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const chart: IChartApi = createChart(ref.current, {
      ...CHART_OPTS,
      height: 320,
    });
    const s = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
    });
    s.setData(
      bars.map((b) => ({
        time: b.t as string,
        open: b.o,
        high: b.h,
        low: b.l,
        close: b.c,
      })),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars]);
  return <div ref={ref} className="h-80 w-full" />;
}

function AreaChart({
  bars,
  prevClose,
}: {
  bars: Bar[];
  prevClose: number | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const last = bars[bars.length - 1].c;
    const up = prevClose == null || last >= prevClose;
    const color = up ? UP : DOWN;
    const chart = createChart(ref.current, { ...CHART_OPTS, height: 320 });
    const s = chart.addSeries(AreaSeries, {
      lineColor: color,
      topColor: up ? "rgba(240,85,92,0.25)" : "rgba(36,185,129,0.25)",
      bottomColor: "rgba(0,0,0,0)",
      lineWidth: 2,
    });
    s.setData(
      bars.map((b) => ({ time: b.t as UTCTimestamp, value: b.c })),
    );
    if (prevClose != null) {
      s.createPriceLine({
        price: prevClose,
        color: "#63615b",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "昨收",
      });
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, prevClose]);
  return <div ref={ref} className="h-80 w-full" />;
}

function TradeTable({
  bars,
  prevClose,
}: {
  bars: Bar[];
  prevClose: number | null;
}) {
  if (bars.length === 0) {
    return (
      <div>
        <SectionTitle>當日交易明細</SectionTitle>
        <Empty msg="無當日分時資料。" />
      </div>
    );
  }
  // 最新在上
  const rows = [...bars].reverse();
  return (
    <div>
      <SectionTitle>當日交易明細（每分鐘）</SectionTitle>
      <div className="max-h-80 overflow-y-auto rounded-xl border border-line-soft">
        <table className="w-full">
          <thead className="sticky top-0 bg-panel">
            <tr className="border-b border-line-soft font-mono text-[10px] tracking-wider text-ink-faint uppercase">
              <th className="px-4 py-2 text-left">時間</th>
              <th className="px-4 py-2 text-right">成交</th>
              <th className="px-4 py-2 text-right">漲跌</th>
              <th className="px-4 py-2 text-right">最高</th>
              <th className="px-4 py-2 text-right">最低</th>
              <th className="px-4 py-2 text-right">量</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b, i) => {
              const chg = prevClose ? (b.c - prevClose) / prevClose : null;
              return (
                <tr
                  key={i}
                  className="border-b border-line-soft/50 font-mono text-sm tnum last:border-0"
                >
                  <td className="px-4 py-1.5 text-left text-ink-dim">
                    {fmtTime(b.t)}
                  </td>
                  <td className={`px-4 py-1.5 text-right ${dirColor(chg)}`}>
                    {b.c.toFixed(2)}
                  </td>
                  <td className={`px-4 py-1.5 text-right ${dirColor(chg)}`}>
                    {chg != null ? `${chg > 0 ? "+" : ""}${(chg * 100).toFixed(2)}%` : "—"}
                  </td>
                  <td className="px-4 py-1.5 text-right text-ink-dim">{b.h.toFixed(2)}</td>
                  <td className="px-4 py-1.5 text-right text-ink-dim">{b.l.toFixed(2)}</td>
                  <td className="px-4 py-1.5 text-right text-ink-dim">
                    {b.v.toLocaleString("zh-TW")}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function fmtTime(t: string | number): string {
  if (typeof t === "number") {
    const d = new Date(t * 1000);
    return d.toISOString().slice(11, 16); // 已含 gmtoffset，顯示台北時間
  }
  return String(t);
}

function TabBtn({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md px-3 py-1 text-sm transition-colors ${
        active
          ? "bg-gold/15 text-gold"
          : disabled
            ? "cursor-not-allowed text-ink-faint/50"
            : "text-ink-dim hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}

function Empty({ msg }: { msg: string }) {
  return (
    <div className="flex h-40 items-center justify-center rounded-xl border border-line-soft bg-panel-2/30 text-sm text-ink-faint">
      {msg}
    </div>
  );
}
