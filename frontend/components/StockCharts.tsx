"use client";

import {
  AreaSeries,
  CandlestickSeries,
  createChart,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type Bar,
  type ChartResponse,
  type ScorePoint,
  type Tick,
} from "@/lib/api";
import { SectionTitle } from "@/components/Card";
import { FlowsPanel } from "@/components/FlowsPanel";

const UP = "#f0555c"; // 漲/買/外盤 → 紅
const DOWN = "#24b981"; // 跌/賣/內盤 → 綠

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

function hms(t: number): string {
  return new Date(t * 1000).toISOString().slice(11, 19);
}

/** 二分找最接近 time 的 tick index（ticks 依 t 升冪）。 */
function nearestTickIdx(ticks: Tick[], t: number): number {
  if (ticks.length === 0) return -1;
  let lo = 0,
    hi = ticks.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (ticks[mid].t < t) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(ticks[lo - 1].t - t) <= Math.abs(ticks[lo].t - t)) return lo - 1;
  return lo;
}

type Tab = "daily" | "intraday" | "scores" | "flows";

const TABS: readonly Tab[] = ["daily", "intraday", "scores", "flows"];

export function StockCharts({
  symbol,
  initialTab,
}: {
  symbol: string;
  initialTab?: string;
}) {
  const [data, setData] = useState<ChartResponse | null>(null);
  const [ticks, setTicks] = useState<Tick[]>([]);
  const [scores, setScores] = useState<ScorePoint[]>([]);
  const [tab, setTab] = useState<Tab>(
    TABS.includes(initialTab as Tab) ? (initialTab as Tab) : "daily",
  );
  const [tickIdx, setTickIdx] = useState<number | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.chart(symbol).then((d) => !cancelled && setData(d)).catch(() => !cancelled && setErr(true));
    api.ticks(symbol).then((r) => !cancelled && setTicks(r.ticks)).catch(() => {});
    api.scores(symbol).then((r) => !cancelled && setScores(r.points)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (err) return <Empty msg="圖表資料暫時無法取得。" />;
  if (!data) return <Empty msg="載入圖表中…" />;

  const hasIntraday = data.intraday.length > 0;
  const hasScores = scores.length > 0;
  const selectedTime = tickIdx != null && ticks[tickIdx] ? ticks[tickIdx].t : null;

  const onChartSelect = (t: number) => {
    const idx = nearestTickIdx(ticks, t);
    if (idx >= 0) setTickIdx(idx);
  };
  const onRowSelect = (idx: number) => {
    setTab("intraday");
    setTickIdx(idx);
  };

  return (
    <div className="space-y-6">
      <div>
        <div className="mb-4 flex items-center justify-between">
          <SectionTitle>{tab === "flows" ? "主力進出" : "價格走勢"}</SectionTitle>
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
            <TabBtn
              active={tab === "scores"}
              onClick={() => setTab("scores")}
              disabled={!hasScores}
            >
              籌碼分數
            </TabBtn>
            <TabBtn active={tab === "flows"} onClick={() => setTab("flows")}>
              主力進出
            </TabBtn>
          </div>
        </div>
        {tab === "daily" ? (
          <CandleChart bars={data.daily} scores={scores} />
        ) : tab === "intraday" ? (
          <AreaChart
            bars={data.intraday}
            prevClose={data.prev_close}
            selectedTime={selectedTime}
            onSelect={onChartSelect}
          />
        ) : tab === "scores" ? (
          <ScoreChart points={scores} />
        ) : (
          <FlowsPanel symbol={symbol} />
        )}
      </div>

      {tab !== "flows" && (
        <TickTable
          ticks={ticks}
          prevClose={data.prev_close}
          selectedIdx={tickIdx}
          onSelect={onRowSelect}
        />
      )}
    </div>
  );
}

type OHLCInfo = {
  time: string;
  o: number;
  h: number;
  l: number;
  c: number;
  chg: number | null;
};

function CandleChart({ bars, scores }: { bars: Bar[]; scores: ScorePoint[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [info, setInfo] = useState<OHLCInfo | null>(null);
  // chip_score 副圖與 K 線同時間軸；顯示當根(crosshair)分數
  const [chip, setChip] = useState<number | null>(null);

  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const chart = createChart(ref.current, {
      ...BASE_OPTS,
      height: 420,
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
      bars.map((b) => ({ time: b.t as string, open: b.o, high: b.h, low: b.l, close: b.c })),
    );

    // Chip Score 副圖(pane 1)——與價格時間軸對齊，呈現籌碼分數演變
    const chipByTime = new Map<string, number>();
    const chipPts = scores.filter((p) => p.chip_score != null);
    if (chipPts.length > 0) {
      const cs = chart.addSeries(
        LineSeries,
        {
          color: "#d9a441",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: true,
          crosshairMarkerVisible: true,
        },
        1,
      );
      cs.setData(chipPts.map((p) => ({ time: p.t as string, value: p.chip_score })));
      cs.createPriceLine({
        price: 50,
        color: "#63615b",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "中性",
      });
      for (const p of chipPts) chipByTime.set(p.t as string, p.chip_score);
      const panes = chart.panes();
      if (panes.length > 1) {
        panes[0].setStretchFactor(3);
        panes[1].setStretchFactor(1);
      }
    }
    chart.timeScale().fitContent();

    // 前一根收盤，供漲跌計算（依時間對齊）
    const prevCloseByTime = new Map<string, number>();
    for (let i = 1; i < bars.length; i++) {
      prevCloseByTime.set(bars[i].t as string, bars[i - 1].c);
    }
    const showLast = () => {
      const b = bars[bars.length - 1];
      const pc = prevCloseByTime.get(b.t as string) ?? null;
      setInfo({
        time: b.t as string,
        o: b.o,
        h: b.h,
        l: b.l,
        c: b.c,
        chg: pc != null ? (b.c - pc) / pc : null,
      });
      setChip(chipByTime.get(b.t as string) ?? null);
    };
    showLast();

    chart.subscribeCrosshairMove((param) => {
      if (param.time == null || !param.point) {
        showLast();
        return;
      }
      const bar = param.seriesData.get(s) as
        | { open: number; high: number; low: number; close: number }
        | undefined;
      if (!bar) {
        showLast();
        return;
      }
      const t = param.time as string;
      const pc = prevCloseByTime.get(t) ?? null;
      setInfo({
        time: t,
        o: bar.open,
        h: bar.high,
        l: bar.low,
        c: bar.close,
        chg: pc != null ? (bar.close - pc) / pc : null,
      });
      setChip(chipByTime.get(t) ?? null);
    });

    const ro = new ResizeObserver(() => chart.timeScale().fitContent());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [bars, scores]);

  return (
    <div className="relative">
      {info && (
        <div className="pointer-events-none absolute left-2 top-2 z-10 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-lg border border-line-soft bg-panel/85 px-3 py-1.5 font-mono text-xs tnum backdrop-blur-sm">
          <span className="text-ink-dim">{info.time}</span>
          <OHLCItem label="開" value={info.o} />
          <OHLCItem label="高" value={info.h} />
          <OHLCItem label="低" value={info.l} />
          <OHLCItem label="收" value={info.c} />
          <span
            className={
              info.chg == null || info.chg === 0
                ? "text-ink-dim"
                : info.chg > 0
                  ? "text-up"
                  : "text-down"
            }
          >
            {info.chg != null
              ? `${info.chg > 0 ? "+" : ""}${(info.chg * 100).toFixed(2)}%`
              : "—"}
          </span>
          {chip != null && (
            <span className="text-ink-faint">
              <span style={{ color: "#d9a441" }}>●</span> Chip
              <span className="ml-0.5 text-ink">{chip.toFixed(1)}</span>
            </span>
          )}
        </div>
      )}
      <div ref={ref} className="h-[420px] w-full" />
    </div>
  );
}

function OHLCItem({ label, value }: { label: string; value: number }) {
  return (
    <span className="text-ink-faint">
      {label}
      <span className="ml-0.5 text-ink">{value.toFixed(2)}</span>
    </span>
  );
}

function AreaChart({
  bars,
  prevClose,
  selectedTime,
  onSelect,
}: {
  bars: Bar[];
  prevClose: number | null;
  selectedTime: number | null;
  onSelect: (t: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const lastRef = useRef<number>(0);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const last = bars[bars.length - 1].c;
    lastRef.current = last;
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
    s.setData(bars.map((b) => ({ time: b.t as UTCTimestamp, value: b.c })));
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

  useEffect(() => {
    const chart = chartRef.current;
    const s = seriesRef.current;
    if (!chart || !s) return;
    if (selectedTime == null) {
      chart.clearCrosshairPosition();
      return;
    }
    chart.setCrosshairPosition(lastRef.current, selectedTime as UTCTimestamp, s);
  }, [selectedTime]);

  return <div ref={ref} className="h-[340px] w-full" />;
}

type ScoreKey = "chip_score" | "institutional" | "holder" | "intraday" | "market";

const SCORE_LINES: {
  key: ScoreKey;
  label: string;
  color: string;
  width: 1 | 2 | 3;
}[] = [
  { key: "chip_score", label: "Chip", color: "#d9a441", width: 3 },
  { key: "institutional", label: "法人", color: "#6ea8fe", width: 1 },
  { key: "holder", label: "集中", color: "#b98cff", width: 1 },
  { key: "intraday", label: "盤中", color: "#4bc0c0", width: 1 },
  { key: "market", label: "大盤", color: "#8a95a5", width: 1 },
];

type ScoreInfo = { time: string } & Record<ScoreKey, number | null>;

function toInfo(p: ScorePoint): ScoreInfo {
  return {
    time: p.t,
    chip_score: p.chip_score,
    institutional: p.institutional,
    holder: p.holder,
    intraday: p.intraday,
    market: p.market,
  };
}

/** Chip Score 與四維分項的每日時序(0~100,50 為中性)。 */
function ScoreChart({ points }: { points: ScorePoint[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [info, setInfo] = useState<ScoreInfo | null>(null);

  useEffect(() => {
    if (!ref.current || points.length === 0) return;
    const chart = createChart(ref.current, {
      ...BASE_OPTS,
      height: 340,
      timeScale: { borderColor: "#26262e", fixLeftEdge: true, fixRightEdge: true },
    });
    let chipSeries: ISeriesApi<"Line"> | null = null;
    for (const ln of SCORE_LINES) {
      const s = chart.addSeries(LineSeries, {
        color: ln.color,
        lineWidth: ln.width,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: ln.key === "chip_score",
      });
      s.setData(
        points
          .filter((p) => p[ln.key] != null)
          .map((p) => ({ time: p.t as string, value: p[ln.key] as number })),
      );
      if (ln.key === "chip_score") chipSeries = s;
    }
    chipSeries?.createPriceLine({
      price: 50,
      color: "#63615b",
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: true,
      title: "中性",
    });
    chart.timeScale().fitContent();

    const showLast = () => setInfo(toInfo(points[points.length - 1]));
    showLast();
    chart.subscribeCrosshairMove((param) => {
      if (param.time == null) {
        showLast();
        return;
      }
      const p = points.find((x) => x.t === (param.time as string));
      if (p) setInfo(toInfo(p));
    });

    const ro = new ResizeObserver(() => chart.timeScale().fitContent());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [points]);

  return (
    <div className="relative">
      {info && (
        <div className="pointer-events-none absolute left-2 top-2 z-10 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-lg border border-line-soft bg-panel/85 px-3 py-1.5 font-mono text-xs tnum backdrop-blur-sm">
          <span className="text-ink-dim">{info.time}</span>
          {SCORE_LINES.map((ln) => (
            <span key={ln.key} className="text-ink-faint">
              <span style={{ color: ln.color }}>●</span> {ln.label}
              <span className="ml-0.5 text-ink">
                {info[ln.key] != null ? info[ln.key]!.toFixed(1) : "—"}
              </span>
            </span>
          ))}
        </div>
      )}
      <div ref={ref} className="h-[340px] w-full" />
    </div>
  );
}

function TickTable({
  ticks,
  prevClose,
  selectedIdx,
  onSelect,
}: {
  ticks: Tick[];
  prevClose: number | null;
  selectedIdx: number | null;
  onSelect: (idx: number) => void;
}) {
  const activeRef = useRef<HTMLTableRowElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selectedIdx == null) return;
    const c = scrollRef.current;
    const row = activeRef.current;
    if (!c || !row) return;
    const thead = c.querySelector("thead") as HTMLElement | null;
    c.scrollTop = row.offsetTop - (thead?.offsetHeight ?? 0);
  }, [selectedIdx]);

  // 最新在上：以原索引配對，方便與圖表連動
  const ordered = useMemo(
    () => ticks.map((t, i) => ({ t, i })).reverse(),
    [ticks],
  );

  if (ticks.length === 0) {
    return (
      <div>
        <SectionTitle>當日逐筆明細</SectionTitle>
        <Empty msg="無當日逐筆資料（非交易日或來源暫無）。" />
      </div>
    );
  }

  return (
    <div>
      <SectionTitle>當日逐筆明細（{ticks.length} 筆 · 與分時圖雙向連動）</SectionTitle>
      <div
        ref={scrollRef}
        className="max-h-96 overflow-auto rounded-xl border border-line-soft"
      >
        <table className="w-full min-w-[520px]">
          <thead className="sticky top-0 z-10 bg-panel">
            <tr className="border-b border-line-soft font-mono text-[10px] tracking-wider text-ink-faint whitespace-nowrap uppercase">
              <th className="px-4 py-2 text-left">時間</th>
              <th className="px-4 py-2 text-right">成交</th>
              <th className="px-4 py-2 text-right">漲跌</th>
              <th className="px-4 py-2 text-center">內外盤</th>
              <th className="px-4 py-2 text-right">量</th>
              <th className="px-4 py-2 text-right">買價</th>
              <th className="px-4 py-2 text-right">賣價</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map(({ t, i }) => {
              const chg = prevClose ? (t.price - prevClose) / prevClose : null;
              const active = selectedIdx === i;
              const buy = t.side > 0;
              const sell = t.side < 0;
              const sideColor = buy ? "text-up" : sell ? "text-down" : "text-ink-faint";
              const chgColor =
                chg == null || chg === 0 ? "text-ink-dim" : chg > 0 ? "text-up" : "text-down";
              return (
                <tr
                  key={i}
                  ref={active ? activeRef : undefined}
                  onClick={() => onSelect(i)}
                  className={`cursor-pointer whitespace-nowrap border-b border-line-soft/40 font-mono text-sm tnum transition-colors last:border-0 ${
                    active ? "bg-gold/10" : "hover:bg-white/[0.03]"
                  }`}
                >
                  <td className={`px-4 py-1 text-left ${active ? "text-gold" : "text-ink-dim"}`}>
                    {t.time}
                  </td>
                  <td className={`px-4 py-1 text-right ${chgColor}`}>{t.price.toFixed(2)}</td>
                  <td className={`px-4 py-1 text-right ${chgColor}`}>
                    {chg != null ? `${chg > 0 ? "+" : ""}${(chg * 100).toFixed(2)}%` : "—"}
                  </td>
                  <td className={`px-4 py-1 text-center ${sideColor}`}>
                    {buy ? "外盤" : sell ? "內盤" : "—"}
                  </td>
                  <td className="px-4 py-1 text-right text-ink">{t.volume.toLocaleString("zh-TW")}</td>
                  <td className="px-4 py-1 text-right text-down">{t.bid?.toFixed(2) ?? "—"}</td>
                  <td className="px-4 py-1 text-right text-up">{t.ask?.toFixed(2) ?? "—"}</td>
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
