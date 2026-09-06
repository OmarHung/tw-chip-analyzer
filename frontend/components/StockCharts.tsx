"use client";

import {
  AreaSeries,
  CandlestickSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { api, type Bar, type ChartResponse } from "@/lib/api";
import { SectionTitle } from "@/components/Card";
import { dirColor } from "@/lib/format";

const UP = "#f0555c"; // 漲/紅
const DOWN = "#24b981"; // 跌/綠

const BASE_OPTS = {
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
  crosshair: { mode: 0 as const },
  autoSize: true,
};

/** 把 UTCTimestamp(已含 gmtoffset) 格式化為台北 時:分:秒。 */
function hms(t: number): string {
  return new Date(t * 1000).toISOString().slice(11, 19);
}
function hm(t: number): string {
  return new Date(t * 1000).toISOString().slice(11, 16);
}

type Tab = "daily" | "intraday";

export function StockCharts({ symbol }: { symbol: string }) {
  const [data, setData] = useState<ChartResponse | null>(null);
  const [tab, setTab] = useState<Tab>("daily");
  const [selected, setSelected] = useState<number | null>(null);
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

  if (err) return <Empty msg="圖表資料暫時無法取得。" />;
  if (!data) return <Empty msg="載入圖表中…" />;

  const hasIntraday = data.intraday.length > 0;

  const selectRow = (t: number) => {
    setTab("intraday");
    setSelected(t);
  };

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
          <AreaChart
            bars={data.intraday}
            prevClose={data.prev_close}
            selected={selected}
            onSelect={setSelected}
          />
        )}
      </div>

      <TradeTable
        bars={data.intraday}
        prevClose={data.prev_close}
        selected={selected}
        onSelect={selectRow}
      />
    </div>
  );
}

function CandleChart({ bars }: { bars: Bar[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const chart = createChart(ref.current, {
      ...BASE_OPTS,
      height: 340,
      timeScale: { borderColor: "#26262e", fixLeftEdge: true, fixRightEdge: true },
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
    const ro = new ResizeObserver(() => chart.timeScale().fitContent());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [bars]);
  return <div ref={ref} className="h-[340px] w-full" />;
}

function AreaChart({
  bars,
  prevClose,
  selected,
  onSelect,
}: {
  bars: Bar[];
  prevClose: number | null;
  selected: number | null;
  onSelect: (t: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const priceMap = useRef<Map<number, number>>(new Map());
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const last = bars[bars.length - 1].c;
    const up = prevClose == null || last >= prevClose;
    const color = up ? UP : DOWN;
    const chart = createChart(ref.current, {
      ...BASE_OPTS,
      height: 340,
      timeScale: {
        borderColor: "#26262e",
        timeVisible: true,
        secondsVisible: true,
        fixLeftEdge: true,
        fixRightEdge: true,
      },
      localization: { timeFormatter: (t: unknown) => hms(t as number) },
    });
    const s = chart.addSeries(AreaSeries, {
      lineColor: color,
      topColor: up ? "rgba(240,85,92,0.25)" : "rgba(36,185,129,0.25)",
      bottomColor: "rgba(0,0,0,0)",
      lineWidth: 2,
    });
    priceMap.current = new Map();
    s.setData(
      bars.map((b) => {
        priceMap.current.set(b.t as number, b.c);
        return { time: b.t as UTCTimestamp, value: b.c };
      }),
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
    chartRef.current = chart;
    seriesRef.current = s;
    // 反向連動：點分時圖 → 選到對應明細
    chart.subscribeClick((param) => {
      if (param.time != null) onSelectRef.current(Number(param.time));
    });
    const ro = new ResizeObserver(() => chart.timeScale().fitContent());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [bars, prevClose]);

  // 點選交易明細 → 於分時圖畫垂直定位線（crosshair）
  useEffect(() => {
    const chart = chartRef.current;
    const s = seriesRef.current;
    if (!chart || !s) return;
    if (selected == null) {
      chart.clearCrosshairPosition();
      return;
    }
    const price = priceMap.current.get(selected);
    if (price != null) {
      chart.setCrosshairPosition(price, selected as UTCTimestamp, s);
    }
  }, [selected]);

  return <div ref={ref} className="h-[340px] w-full" />;
}

function TradeTable({
  bars,
  prevClose,
  selected,
  onSelect,
}: {
  bars: Bar[];
  prevClose: number | null;
  selected: number | null;
  onSelect: (t: number) => void;
}) {
  if (bars.length === 0) {
    return (
      <div>
        <SectionTitle>當日交易明細</SectionTitle>
        <Empty msg="無當日分時資料。" />
      </div>
    );
  }
  const rows = [...bars].reverse(); // 最新在上
  const activeRef = useRef<HTMLTableRowElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selected == null) return;
    const c = scrollRef.current;
    const row = activeRef.current;
    if (!c || !row) return;
    // 捲到選取列，並讓它落在 sticky 表頭下方（顯示為第一列，不被擋住）
    const thead = c.querySelector("thead") as HTMLElement | null;
    c.scrollTop = row.offsetTop - (thead?.offsetHeight ?? 0);
  }, [selected]);

  return (
    <div>
      <SectionTitle>當日交易明細（每分鐘 · 與分時圖雙向連動）</SectionTitle>
      <div
        ref={scrollRef}
        className="max-h-80 overflow-auto rounded-xl border border-line-soft"
      >
        <table className="w-full min-w-[420px]">
          <thead className="sticky top-0 z-10 bg-panel">
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
            {rows.map((b) => {
              const t = b.t as number;
              const chg = prevClose ? (b.c - prevClose) / prevClose : null;
              const active = selected === t;
              return (
                <tr
                  key={t}
                  ref={active ? activeRef : undefined}
                  onClick={() => onSelect(t)}
                  className={`cursor-pointer border-b border-line-soft/50 font-mono text-sm tnum transition-colors last:border-0 ${
                    active ? "bg-gold/10" : "hover:bg-white/[0.03]"
                  }`}
                >
                  <td className="px-4 py-1.5 text-left">
                    <span className={active ? "text-gold" : "text-ink-dim"}>
                      {hm(t)}
                    </span>
                  </td>
                  <td className={`px-4 py-1.5 text-right ${dirColor(chg)}`}>
                    {b.c.toFixed(2)}
                  </td>
                  <td className={`px-4 py-1.5 text-right ${dirColor(chg)}`}>
                    {chg != null
                      ? `${chg > 0 ? "+" : ""}${(chg * 100).toFixed(2)}%`
                      : "—"}
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
