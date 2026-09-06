"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Change } from "@/components/Change";
import {
  api,
  type CostState,
  type DivergenceScanRow,
} from "@/lib/api";
import { fmtPrice, fmtTurnover } from "@/lib/format";

type Side = "bullish_div" | "bearish_div";

const SIDES: { key: Side; label: string; sub: string; tone: string }[] = [
  {
    key: "bullish_div",
    label: "正背離",
    sub: "價跌 · 主力買 → 疑逢低吸籌",
    tone: "text-up",
  },
  {
    key: "bearish_div",
    label: "負背離",
    sub: "價漲 · 主力賣 → 疑逢高出貨",
    tone: "text-down",
  },
];

const COST_ZH: Record<CostState, string> = {
  profit: "浮盈",
  loss: "套牢",
  flat: "打平",
  unknown: "—",
};

function fmtPct(v: number | null): string {
  if (v == null) return "—";
  const p = v * 100;
  return `${p > 0 ? "+" : ""}${p.toFixed(1)}%`;
}

function fmtLots(v: number): string {
  const s = v > 0 ? "+" : "";
  if (Math.abs(v) >= 10000) return `${s}${(v / 10000).toFixed(1)} 萬張`;
  return `${s}${Math.round(v).toLocaleString("zh-TW")} 張`;
}

export default function DivergencePage() {
  const [side, setSide] = useState<Side>("bullish_div");
  const [rows, setRows] = useState<DivergenceScanRow[]>([]);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [win, setWin] = useState<number>(60);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .divergenceScan({ status: side, limit: 100 })
      .then((res) => {
        if (cancelled) return;
        setRows(res.rows);
        setAsOf(res.as_of);
        setWin(res.window);
        setError(null);
      })
      .catch(() => !cancelled && setError("無法連線後端 API"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [side]);

  const active = SIDES.find((s) => s.key === side)!;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-4xl leading-none">
          主力<span className="text-gold">背離</span>
        </h1>
        <p className="mt-2 font-mono text-xs tracking-wider text-ink-faint">
          {asOf ?? "—"} · {win} 日窗 · {rows.length} 檔{loading && " · 載入中"}
        </p>
      </div>

      {/* 說明 + 誠實 caveat */}
      <div className="rounded-2xl border border-line-soft bg-panel-2/40 px-4 py-3 text-xs text-ink-dim sm:px-6">
        量價背離＝股價與主力（三大法人）方向分歧。回測顯示 60 日正/負背離對未來報酬
        有方向正確的區辨力（正背離偏強、負背離偏弱），但樣本僅約半年、非單調，
        <span className="text-ink">尚未達可單獨交易門檻——請作為多訊號交叉驗證之一。</span>
      </div>

      {/* 切換 */}
      <div className="flex gap-2">
        {SIDES.map((s) => (
          <button
            key={s.key}
            onClick={() => setSide(s.key)}
            className={`flex-1 rounded-xl border px-4 py-3 text-left transition-colors ${
              side === s.key
                ? "border-gold/40 bg-gold/5"
                : "border-line-soft bg-panel/50 hover:bg-white/[0.02]"
            }`}
          >
            <div className={`font-mono text-sm font-bold ${s.tone}`}>{s.label}</div>
            <div className="mt-0.5 text-[11px] text-ink-faint">{s.sub}</div>
          </button>
        ))}
      </div>

      {error ? (
        <div className="rounded-2xl border border-line-soft bg-panel/70 p-10 text-center text-ink-dim">
          {error}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-line-soft bg-panel/50">
          <table className="w-full">
            <thead>
              <tr className="border-b border-line-soft font-mono text-[10px] tracking-[0.12em] text-ink-faint uppercase">
                <Th>股票</Th>
                <Th right>收盤</Th>
                <Th right>漲跌</Th>
                <Th right>佔量比</Th>
                <Th right hideSm>{win}日區間</Th>
                <Th right hideSm>主力淨買超</Th>
                <Th right>主力成本</Th>
                <Th right hideSm>成交額</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr
                  key={r.symbol}
                  className="reveal border-b border-line-soft/60 transition-colors last:border-0 hover:bg-white/[0.02]"
                  style={{ animationDelay: `${Math.min(i * 12, 360)}ms` }}
                >
                  <td className="py-3 pl-4 pr-2 sm:pl-5 sm:pr-3">
                    <Link
                      href={`/stocks/${r.symbol}?tab=flows`}
                      className="group flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2"
                    >
                      <span className="font-mono text-sm font-medium text-ink group-hover:text-gold">
                        {r.symbol}
                      </span>
                      <span className="max-w-[7rem] truncate text-xs text-ink-dim sm:max-w-none sm:text-sm">
                        {r.name}
                      </span>
                    </Link>
                  </td>
                  <Td right mono>
                    {r.price != null ? fmtPrice(r.price) : "—"}
                  </Td>
                  <td className="px-2 py-3 text-right sm:px-3">
                    <Change pct={r.change_pct} className="text-sm" />
                  </td>
                  <Td right>
                    <span
                      className={`font-mono font-bold tnum ${
                        (r.flow_ratio ?? 0) > 0 ? "text-up" : "text-down"
                      }`}
                    >
                      {fmtPct(r.flow_ratio)}
                    </span>
                  </Td>
                  <Td right mono hideSm>
                    <span className={(r.price_return ?? 0) > 0 ? "text-up" : "text-down"}>
                      {fmtPct(r.price_return)}
                    </span>
                  </Td>
                  <Td right mono dim hideSm>
                    {fmtLots(r.inst_net)}
                  </Td>
                  <Td right mono dim>
                    {COST_ZH[r.cost_state]}
                    {r.premium_pct != null && (
                      <span
                        className={`ml-1 ${
                          r.premium_pct > 0 ? "text-up" : "text-down"
                        }`}
                      >
                        {fmtPct(r.premium_pct)}
                      </span>
                    )}
                  </Td>
                  <Td right mono dim hideSm>
                    {fmtTurnover(r.turnover)}
                  </Td>
                </tr>
              ))}
              {!loading && rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-ink-faint">
                    目前無{active.label}標的
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Th({
  children,
  right,
  hideSm,
}: {
  children: React.ReactNode;
  right?: boolean;
  hideSm?: boolean;
}) {
  return (
    <th
      className={`whitespace-nowrap px-2 py-3 font-medium first:pl-4 sm:px-3 sm:first:pl-5 ${
        right ? "text-right" : "text-left"
      } ${hideSm ? "hidden md:table-cell" : ""}`}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  right,
  mono,
  dim,
  hideSm,
}: {
  children: React.ReactNode;
  right?: boolean;
  mono?: boolean;
  dim?: boolean;
  hideSm?: boolean;
}) {
  return (
    <td
      className={`px-2 py-3 text-sm sm:px-3 ${right ? "text-right" : ""} ${
        mono ? "font-mono tnum" : ""
      } ${dim ? "text-ink-dim" : "text-ink"} ${hideSm ? "hidden md:table-cell" : ""}`}
    >
      {children}
    </td>
  );
}
