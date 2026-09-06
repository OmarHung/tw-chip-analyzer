"use client";

import {
  BaselineSeries,
  createChart,
  HistogramSeries,
  LineSeries,
  type ISeriesApi,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  type CostBasis,
  type CostBasisPoint,
  type CostState,
  type DivergenceItem,
  type DivergenceStatus,
  type FlowPoint,
  type FlowsResponse,
  type OrderFlowResponse,
} from "@/lib/api";

const UP = "#f0555c"; // 買超 → 紅（台股語意）
const DOWN = "#24b981"; // 賣超 → 綠
const PRICE = "#9a968c"; // 價格線（中性）
const COST_COLOR = "#e0873a"; // 主力估算成本線（琥珀，虛線）

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
} as const;

type SeriesKey = "inst_total" | "foreign" | "trust" | "dealer";

const SERIES: { key: SeriesKey; label: string; color: string; width: 1 | 2 | 3 }[] =
  [
    { key: "inst_total", label: "三大法人", color: "#d9a441", width: 3 },
    { key: "foreign", label: "外資", color: "#6ea8fe", width: 2 },
    { key: "trust", label: "投信", color: "#b98cff", width: 1 },
    { key: "dealer", label: "自營", color: "#4bc0c0", width: 1 },
  ];

const RANGES: { label: string; days: number }[] = [
  { label: "近1月", days: 30 },
  { label: "近3月", days: 90 },
  { label: "近6月", days: 180 },
];

/** 張數格式化（帶正負號；≥1 萬張以「萬張」呈現）。 */
function fmtLots(v: number | null | undefined, sign = true): string {
  if (v == null) return "—";
  const s = sign && v > 0 ? "+" : "";
  const a = Math.abs(v);
  if (a >= 10000) return `${s}${(v / 10000).toFixed(1)} 萬張`;
  return `${s}${Math.round(v).toLocaleString("zh-TW")} 張`;
}

function toneClass(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-ink-dim";
  return v > 0 ? "text-up" : "text-down";
}

export function FlowsPanel({ symbol }: { symbol: string }) {
  const [days, setDays] = useState(90);
  const [data, setData] = useState<FlowsResponse | null>(null);
  const [order, setOrder] = useState<OrderFlowResponse | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(false);
    api
      .flows(symbol, days)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setErr(true));
    return () => {
      cancelled = true;
    };
  }, [symbol, days]);

  useEffect(() => {
    let cancelled = false;
    api
      .orderflow(symbol)
      .then((r) => !cancelled && setOrder(r))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [symbol]);

  if (err)
    return <div className="text-sm text-ink-faint">主力進出資料暫時無法取得。</div>;
  if (!data) return <div className="text-sm text-ink-faint">載入主力進出資料中…</div>;

  const { points, summary, tdcc, divergence, cost_basis } = data;
  const empty = points.length === 0;

  return (
    <div className="space-y-8">
      {/* 區間切換 */}
      <div className="flex items-center justify-between">
        <div className="font-mono text-[11px] tracking-[0.15em] text-ink-faint uppercase">
          {data.count} 個交易日 · 買超為正（張）
        </div>
        <div className="flex gap-1 rounded-lg border border-line-soft bg-panel-2/50 p-0.5">
          {RANGES.map((r) => (
            <button
              key={r.days}
              onClick={() => setDays(r.days)}
              className={`rounded-md px-3 py-1 font-mono text-xs transition-colors ${
                days === r.days
                  ? "bg-gold/15 text-gold-bright"
                  : "text-ink-dim hover:text-ink"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {empty ? (
        <div className="rounded-xl border border-line-soft bg-panel-2/40 p-8 text-center text-sm text-ink-faint">
          此區間無主力進出資料。
        </div>
      ) : (
        <>
          {divergence.length > 0 && <DivergenceCard items={divergence} />}

          {summary && <SummaryCards s={summary} />}

          {/* 累積買賣超疊價格（趨勢主軸），疊主力估算成本線 */}
          <section>
            <ChartTitle
              title="累積買賣超 vs 股價"
              hint="累積線持續上揚＝主力持續進場；與股價背離為關鍵訊號"
            />
            {cost_basis && <CostBasisNote cb={cost_basis} />}
            <CumulativeChart points={points} cost={cost_basis?.points ?? null} />
            <Legend
              items={SERIES}
              raw={[
                ...SERIES,
                { label: "股價", color: PRICE },
                { label: "主力估算成本", color: COST_COLOR },
              ]}
            />
          </section>

          {/* 每日買賣超柱狀 */}
          <section>
            <ChartTitle title="每日買賣超" hint="紅買綠賣；切換法人別" />
            <DailyBarChart points={points} />
          </section>

          {/* 融資融券 */}
          <section>
            <ChartTitle
              title="融資 / 融券餘額"
              hint="融資餘額升＝散戶槓桿增；與主力方向背離時留意"
            />
            <MarginChart points={points} />
          </section>
        </>
      )}

      {/* 快照：TDCC 大戶結構 + 盤中大單 */}
      <div className="grid gap-4 md:grid-cols-2">
        <TdccCard tdcc={tdcc} />
        <LargeOrderCard order={order} />
      </div>
    </div>
  );
}

/* ---------- 量價背離卡 ---------- */

const DIV_STYLE: Record<
  DivergenceStatus,
  { tone: string; dot: string; ring: string }
> = {
  bullish_div: { tone: "text-up", dot: "#f0555c", ring: "ring-up/40 bg-up/10" },
  bearish_div: { tone: "text-down", dot: "#24b981", ring: "ring-down/40 bg-down/10" },
  aligned_up: { tone: "text-up", dot: "#f0555c", ring: "ring-white/10 bg-white/5" },
  aligned_down: { tone: "text-down", dot: "#24b981", ring: "ring-white/10 bg-white/5" },
  neutral: { tone: "text-ink-dim", dot: "#8a95a5", ring: "ring-white/10 bg-white/5" },
};

function fmtPct(v: number | null): string {
  if (v == null) return "—";
  const p = v * 100;
  return `${p > 0 ? "+" : ""}${p.toFixed(1)}%`;
}

function DivergenceCard({ items }: { items: DivergenceItem[] }) {
  // 以「背離 > 同向 > 中性」排序,挑最值得看的一則做主敘述
  const rank = (s: DivergenceStatus) =>
    s === "bullish_div" || s === "bearish_div" ? 0 : s === "neutral" ? 2 : 1;
  const lead = [...items].sort((a, b) => rank(a.status) - rank(b.status))[0];
  const st = DIV_STYLE[lead.status];

  return (
    <div className="rounded-2xl border border-line-soft bg-panel/70 p-5">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="font-mono text-[11px] tracking-[0.2em] text-ink-faint uppercase">
          主力 / 股價背離
        </h3>
        <span
          className={`rounded-full px-2.5 py-0.5 font-mono text-[11px] ring-1 ${st.ring} ${st.tone}`}
        >
          <span style={{ color: st.dot }}>●</span> {lead.window}日 {lead.label}
        </span>
      </div>

      <p className="mb-4 text-sm text-ink-dim">{lead.note}</p>

      <div className="grid gap-3 sm:grid-cols-2">
        {items.map((it) => {
          const s = DIV_STYLE[it.status];
          return (
            <div
              key={it.window}
              className="rounded-xl border border-line-soft bg-panel-2/40 p-3.5"
            >
              <div className="mb-2 flex items-center justify-between">
                <span className="font-mono text-[11px] tracking-wider text-ink-faint uppercase">
                  近 {it.window} 交易日
                </span>
                <span className={`font-mono text-sm font-bold ${s.tone}`}>
                  <span style={{ color: s.dot }}>●</span> {it.label}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono text-xs tnum">
                <MiniKV label="股價區間" value={fmtPct(it.price_return)} tone={toneClass(it.price_return)} />
                <MiniKV
                  label="主力佔成交"
                  value={fmtPct(it.inst_flow_ratio)}
                  tone={toneClass(it.inst_flow_ratio)}
                />
                <MiniKV label="主力淨買超" value={fmtLots(it.inst_net)} tone={toneClass(it.inst_net)} />
                <MiniKV
                  label="價量相關"
                  value={it.price_inst_corr != null ? it.price_inst_corr.toFixed(2) : "—"}
                  tone="text-ink"
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MiniKV({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: string;
}) {
  return (
    <div className="flex items-baseline justify-between">
      <span className="text-ink-faint">{label}</span>
      <span className={tone}>{value}</span>
    </div>
  );
}

/* ---------- 摘要卡 ---------- */

function SummaryCards({ s }: { s: FlowsResponse["summary"] & object }) {
  const streakLabel =
    s.foreign_streak === 0
      ? "—"
      : `${s.foreign_streak > 0 ? "連買" : "連賣"} ${Math.abs(s.foreign_streak)} 日`;
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <SumCard
        label="外資 近5 / 20 / 60 日"
        main={fmtLots(s.foreign_20d)}
        mainTone={toneClass(s.foreign_20d)}
        sub={`5日 ${fmtLots(s.foreign_5d)} · 60日 ${fmtLots(s.foreign_60d)}`}
      />
      <SumCard
        label="三大法人 近5 / 20 / 60 日"
        main={fmtLots(s.inst_20d)}
        mainTone={toneClass(s.inst_20d)}
        sub={`5日 ${fmtLots(s.inst_5d)} · 60日 ${fmtLots(s.inst_60d)}`}
      />
      <SumCard
        label="外資連續動向"
        main={streakLabel}
        mainTone={toneClass(s.foreign_streak)}
        sub="尾端同方向天數"
      />
      <SumCard
        label="融資餘額 20 日變化"
        main={fmtLots(s.margin_chg_20d, true)}
        mainTone={s.margin_chg_20d > 0 ? "text-up" : s.margin_chg_20d < 0 ? "text-down" : "text-ink-dim"}
        sub="散戶槓桿方向"
      />
    </div>
  );
}

function SumCard({
  label,
  main,
  mainTone,
  sub,
}: {
  label: string;
  main: string;
  mainTone: string;
  sub: string;
}) {
  return (
    <div className="rounded-xl border border-line-soft bg-panel-2/40 p-4">
      <div className="font-mono text-[10px] leading-tight tracking-wider text-ink-faint uppercase">
        {label}
      </div>
      <div className={`mt-2 font-mono text-xl font-bold tnum ${mainTone}`}>{main}</div>
      <div className="mt-1 font-mono text-[11px] text-ink-faint tnum">{sub}</div>
    </div>
  );
}

/* ---------- 主力估算成本說明 ---------- */

const COST_STATE_STYLE: Record<CostState, { tone: string; ring: string }> = {
  profit: { tone: "text-up", ring: "ring-up/40 bg-up/10" },
  loss: { tone: "text-down", ring: "ring-down/40 bg-down/10" },
  flat: { tone: "text-ink-dim", ring: "ring-white/10 bg-white/5" },
  unknown: { tone: "text-ink-faint", ring: "ring-white/10 bg-white/5" },
};

function CostBasisNote({ cb }: { cb: CostBasis }) {
  const st = COST_STATE_STYLE[cb.state];
  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs tnum">
      <span
        className={`rounded-full px-2.5 py-0.5 text-[11px] ring-1 ${st.ring} ${st.tone}`}
      >
        {cb.label}
      </span>
      <span className="text-ink-faint">
        估算成本
        <span className="ml-1 text-ink">
          {cb.latest_cost != null ? cb.latest_cost.toFixed(2) : "—"}
        </span>
      </span>
      <span className="text-ink-faint">
        現價
        <span className="ml-1 text-ink">
          {cb.latest_price != null ? cb.latest_price.toFixed(2) : "—"}
        </span>
      </span>
      {cb.premium_pct != null && (
        <span className="text-ink-faint">
          溢價/折價
          <span className={`ml-1 ${toneClass(cb.premium_pct)}`}>
            {fmtPct(cb.premium_pct)}
          </span>
        </span>
      )}
    </div>
  );
}

/* ---------- 累積買賣超 vs 價格 ---------- */

type CumInfo = {
  time: string;
  price: number | null;
} & Record<SeriesKey, number>;

function CumulativeChart({
  points,
  cost,
}: {
  points: FlowPoint[];
  cost: CostBasisPoint[] | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [info, setInfo] = useState<CumInfo | null>(null);

  // 累積和（null 視為 0 貢獻，維持線連續）
  const cum = useMemo(() => {
    const acc: Record<SeriesKey, number> = {
      inst_total: 0,
      foreign: 0,
      trust: 0,
      dealer: 0,
    };
    return points.map((p) => {
      for (const { key } of SERIES) acc[key] += p[key] ?? 0;
      return { t: p.t, price: p.close, ...acc } as CumInfo & { t: string };
    });
  }, [points]);

  useEffect(() => {
    if (!ref.current || cum.length === 0) return;
    const chart = createChart(ref.current, {
      ...BASE_OPTS,
      height: 380,
      timeScale: { borderColor: "#26262e", fixLeftEdge: true, fixRightEdge: true },
    });

    // 主圖：股價（pane 0）
    const priceHasData = points.some((p) => p.close != null);
    let priceSeries: ISeriesApi<"Line"> | null = null;
    if (priceHasData) {
      priceSeries = chart.addSeries(LineSeries, {
        color: PRICE,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      priceSeries.setData(
        points
          .filter((p) => p.close != null)
          .map((p) => ({ time: p.t, value: p.close as number })),
      );
    }

    // 主力估算成本線（pane 0，與股價同軸，虛線）
    if (cost && cost.some((c) => c.cost != null)) {
      const cs = chart.addSeries(LineSeries, {
        color: COST_COLOR,
        lineWidth: 2,
        lineStyle: 2, // dashed
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: false,
      });
      cs.setData(
        cost
          .filter((c) => c.cost != null)
          .map((c) => ({ time: c.t, value: c.cost as number })),
      );
    }

    // 副圖：累積買賣超（pane 1），0 為基準
    const cumSeries: ISeriesApi<"Line">[] = [];
    for (const ln of SERIES) {
      const s = chart.addSeries(
        LineSeries,
        {
          color: ln.color,
          lineWidth: ln.width,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: ln.key === "inst_total",
        },
        1,
      );
      s.setData(cum.map((c) => ({ time: c.t, value: c[ln.key] })));
      cumSeries.push(s);
    }
    cumSeries[0]?.createPriceLine({
      price: 0,
      color: "#63615b",
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: true,
      title: "0",
    });
    const panes = chart.panes();
    if (panes.length > 1) {
      panes[0].setStretchFactor(2);
      panes[1].setStretchFactor(3);
    }
    chart.timeScale().fitContent();

    const showLast = () => setInfo(cum[cum.length - 1]);
    showLast();
    chart.subscribeCrosshairMove((param) => {
      if (param.time == null) {
        showLast();
        return;
      }
      const c = cum.find((x) => x.t === (param.time as string));
      if (c) setInfo(c);
    });

    const ro = new ResizeObserver(() => chart.timeScale().fitContent());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [cum, points, cost]);

  return (
    <div className="relative">
      {info && (
        <div className="pointer-events-none absolute left-2 top-2 z-10 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-lg border border-line-soft bg-panel/85 px-3 py-1.5 font-mono text-xs tnum backdrop-blur-sm">
          <span className="text-ink-dim">{info.time}</span>
          {info.price != null && (
            <span className="text-ink-faint">
              收<span className="ml-0.5 text-ink">{info.price.toFixed(2)}</span>
            </span>
          )}
          {SERIES.map((ln) => (
            <span key={ln.key} className="text-ink-faint">
              <span style={{ color: ln.color }}>●</span> {ln.label}
              <span className={`ml-0.5 ${toneClass(info[ln.key])}`}>
                {fmtLots(info[ln.key])}
              </span>
            </span>
          ))}
        </div>
      )}
      <div ref={ref} className="h-[380px] w-full" />
    </div>
  );
}

/* ---------- 每日買賣超柱狀 ---------- */

function DailyBarChart({ points }: { points: FlowPoint[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [key, setKey] = useState<SeriesKey>("inst_total");

  useEffect(() => {
    if (!ref.current || points.length === 0) return;
    const chart = createChart(ref.current, {
      ...BASE_OPTS,
      height: 240,
      timeScale: { borderColor: "#26262e", fixLeftEdge: true, fixRightEdge: true },
    });
    const s = chart.addSeries(HistogramSeries, {
      priceLineVisible: false,
      base: 0,
    });
    s.setData(
      points
        .filter((p) => p[key] != null)
        .map((p) => ({
          time: p.t,
          value: p[key] as number,
          color: (p[key] as number) >= 0 ? UP : DOWN,
        })),
    );
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.timeScale().fitContent());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [points, key]);

  return (
    <div>
      <div className="mb-2 flex gap-1">
        {SERIES.map((ln) => (
          <button
            key={ln.key}
            onClick={() => setKey(ln.key)}
            className={`rounded-md px-2.5 py-1 font-mono text-[11px] transition-colors ${
              key === ln.key
                ? "bg-white/5 text-ink"
                : "text-ink-faint hover:text-ink-dim"
            }`}
          >
            <span style={{ color: ln.color }}>●</span> {ln.label}
          </button>
        ))}
      </div>
      <div ref={ref} className="h-60 w-full" />
    </div>
  );
}

/* ---------- 融資融券 ---------- */

function MarginChart({ points }: { points: FlowPoint[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const has = points.some((p) => p.margin_balance != null);

  useEffect(() => {
    if (!ref.current || !has) return;
    const chart = createChart(ref.current, {
      ...BASE_OPTS,
      height: 220,
      timeScale: { borderColor: "#26262e", fixLeftEdge: true, fixRightEdge: true },
    });
    const marg = chart.addSeries(LineSeries, {
      color: "#e0873a",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    marg.setData(
      points
        .filter((p) => p.margin_balance != null)
        .map((p) => ({ time: p.t, value: p.margin_balance as number })),
    );
    if (points.some((p) => p.short_balance != null)) {
      const shortS = chart.addSeries(LineSeries, {
        color: "#8a95a5",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        priceScaleId: "left",
      });
      shortS.setData(
        points
          .filter((p) => p.short_balance != null)
          .map((p) => ({ time: p.t, value: p.short_balance as number })),
      );
      chart.priceScale("left").applyOptions({ visible: true, borderColor: "#26262e" });
    }
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => chart.timeScale().fitContent());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [points, has]);

  if (!has)
    return (
      <div className="rounded-xl border border-line-soft bg-panel-2/40 p-6 text-center text-xs text-ink-faint">
        無融資融券資料。
      </div>
    );

  return (
    <div>
      <Legend
        items={[]}
        raw={[
          { label: "融資餘額（右軸）", color: "#e0873a" },
          { label: "融券餘額（左軸）", color: "#8a95a5" },
        ]}
      />
      <div ref={ref} className="h-56 w-full" />
    </div>
  );
}

/* ---------- 快照卡 ---------- */

function TdccCard({ tdcc }: { tdcc: FlowsResponse["tdcc"] }) {
  if (!tdcc)
    return (
      <SnapshotCard title="TDCC 股權結構">
        <div className="text-xs text-ink-faint">無 TDCC 資料。</div>
      </SnapshotCard>
    );
  const rows = [
    { label: "超大戶", v: tdcc.super_large_ratio, color: "#d9a441" },
    { label: "大戶", v: tdcc.large_ratio, color: "#6ea8fe" },
    { label: "中戶", v: tdcc.medium_ratio, color: "#b98cff" },
    { label: "散戶", v: tdcc.retail_ratio, color: "#8a95a5" },
  ];
  return (
    <SnapshotCard title="TDCC 股權結構" sub={`${tdcc.date} · 週快照`}>
      <div className="space-y-2.5">
        {rows.map((r) => (
          <div key={r.label}>
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="text-ink-dim">{r.label}</span>
              <span className="font-mono tnum text-ink">
                {r.v != null ? `${r.v.toFixed(2)}%` : "—"}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-line-soft">
              <div
                className="bar-fill h-full rounded-full"
                style={{ width: `${Math.min(r.v ?? 0, 100)}%`, background: r.color }}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="mt-3 font-mono text-[11px] text-ink-faint">
        趨勢需累積多週後才可判讀（目前僅單週）
      </div>
    </SnapshotCard>
  );
}

function LargeOrderCard({ order }: { order: OrderFlowResponse | null }) {
  if (!order || order.trade_count === 0)
    return (
      <SnapshotCard title="盤中大單淨額">
        <div className="text-xs text-ink-faint">無當日逐筆，無法計算。</div>
      </SnapshotCard>
    );
  return (
    <SnapshotCard title="盤中大單淨額" sub={`${order.date ?? ""} · 當日逐筆`}>
      <div className="grid grid-cols-2 gap-3">
        <MiniStat
          label="大單淨量"
          value={fmtSigned(order.large_delta)}
          tone={toneClass(order.large_delta)}
        />
        <MiniStat
          label="CVD 量差"
          value={fmtSigned(order.cvd_final)}
          tone={toneClass(order.cvd_final)}
        />
        <MiniStat label="大單買" value={fmtNum(order.large_buy)} tone="text-up" />
        <MiniStat label="大單賣" value={fmtNum(order.large_sell)} tone="text-down" />
      </div>
      <div className="mt-3 font-mono text-[11px] text-ink-faint">
        單日資料，尚無法構成三個月趨勢
      </div>
    </SnapshotCard>
  );
}

/* ---------- 共用小件 ---------- */

function SnapshotCard({
  title,
  sub,
  children,
}: {
  title: string;
  sub?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-line-soft bg-panel/70 p-5">
      <div className="mb-4 flex items-baseline justify-between">
        <h3 className="font-mono text-[11px] tracking-[0.2em] text-ink-faint uppercase">
          {title}
        </h3>
        {sub && <span className="font-mono text-[10px] text-ink-faint tnum">{sub}</span>}
      </div>
      {children}
    </div>
  );
}

function MiniStat({
  label,
  value,
  tone = "text-ink",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="rounded-xl border border-line-soft bg-panel-2/40 p-3">
      <div className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
        {label}
      </div>
      <div className={`mt-1 font-mono text-lg font-bold tnum ${tone}`}>{value}</div>
    </div>
  );
}

function ChartTitle({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-3">
      <h3 className="font-mono text-sm text-ink">{title}</h3>
      {hint && <p className="mt-0.5 text-[11px] text-ink-faint">{hint}</p>}
    </div>
  );
}

function Legend({
  items,
  extra,
  raw,
}: {
  items: { label: string; color: string }[];
  extra?: { label: string; color: string };
  raw?: { label: string; color: string }[];
}) {
  const all = raw ?? [...items, ...(extra ? [extra] : [])];
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] text-ink-dim">
      {all.map((it) => (
        <span key={it.label} className="flex items-center gap-1">
          <span style={{ color: it.color }}>●</span>
          {it.label}
        </span>
      ))}
    </div>
  );
}

function fmtSigned(v: number): string {
  const s = v > 0 ? "+" : "";
  return s + Math.round(v).toLocaleString("zh-TW");
}
function fmtNum(v: number): string {
  return Math.round(v).toLocaleString("zh-TW");
}
