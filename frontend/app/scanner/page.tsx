"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ActionBadge } from "@/components/ActionBadge";
import { Change } from "@/components/Change";
import { api, type Action, type ScannerRow } from "@/lib/api";
import { fmtPrice, fmtTurnover, scoreColor } from "@/lib/format";

const ACTIONS: (Action | "")[] = ["", "BUY", "WATCH", "HOLD", "REDUCE", "EXIT", "AVOID"];

export default function ScannerPage() {
  const [minScore, setMinScore] = useState(0);
  const [action, setAction] = useState<Action | "">("");
  const [rows, setRows] = useState<ScannerRow[]>([]);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .scanner({ min_score: minScore, action: action || undefined, limit: 200 })
      .then((res) => {
        if (cancelled) return;
        setRows(res.rows);
        setAsOf(res.as_of);
        setError(null);
      })
      .catch(() => !cancelled && setError("無法連線後端 API"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [minScore, action]);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-display text-4xl leading-none">
            選<span className="italic text-gold">股</span>
          </h1>
          <p className="mt-2 font-mono text-xs tracking-wider text-ink-faint">
            {asOf ?? "—"} · {rows.length} 檔{loading && " · 載入中"}
          </p>
        </div>
      </div>

      {/* 篩選 */}
      <div className="flex flex-wrap items-center gap-8 rounded-2xl border border-line-soft bg-panel/70 px-6 py-4">
        <label className="flex items-center gap-3 text-sm">
          <span className="font-mono text-[11px] tracking-wider text-ink-faint uppercase">
            最低分數
          </span>
          <input
            type="range"
            min={0}
            max={90}
            step={5}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            className="accent-gold"
          />
          <span className="w-8 font-mono text-sm tnum text-gold">{minScore}</span>
        </label>
        <label className="flex items-center gap-3 text-sm">
          <span className="font-mono text-[11px] tracking-wider text-ink-faint uppercase">
            動作
          </span>
          <select
            value={action}
            onChange={(e) => setAction(e.target.value as Action | "")}
            className="rounded-lg border border-line bg-panel-2 px-3 py-1.5 text-sm text-ink outline-none focus:border-gold/50"
          >
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a || "全部"}
              </option>
            ))}
          </select>
        </label>
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
                <Th right>籌碼</Th>
                <Th right>法人</Th>
                <Th right>TDCC</Th>
                <Th center>動作</Th>
                <Th right>成交額</Th>
                <Th right>RR</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr
                  key={r.symbol}
                  className="reveal border-b border-line-soft/60 transition-colors last:border-0 hover:bg-white/[0.02]"
                  style={{ animationDelay: `${Math.min(i * 12, 360)}ms` }}
                >
                  <td className="py-3 pl-5 pr-3">
                    <Link href={`/stocks/${r.symbol}`} className="group flex items-baseline gap-2">
                      <span className="font-mono text-sm font-medium text-ink group-hover:text-gold">
                        {r.symbol}
                      </span>
                      <span className="truncate text-sm text-ink-dim">{r.name}</span>
                    </Link>
                  </td>
                  <Td right mono>{fmtPrice(r.price)}</Td>
                  <td className="px-3 py-3 text-right">
                    <Change pct={r.change_pct} className="text-sm" />
                  </td>
                  <Td right>
                    <span className={`font-mono font-bold tnum ${scoreColor(r.chip_score)}`}>
                      {r.chip_score.toFixed(1)}
                    </span>
                  </Td>
                  <Td right mono dim>{r.institutional.toFixed(0)}</Td>
                  <Td right mono dim>{r.holder.toFixed(0)}</Td>
                  <td className="px-3 py-3 text-center">
                    <ActionBadge action={r.action} />
                  </td>
                  <Td right mono dim>{fmtTurnover(r.turnover)}</Td>
                  <Td right mono dim>{r.rr?.toFixed(1) ?? "—"}</Td>
                </tr>
              ))}
              {!loading && rows.length === 0 && (
                <tr>
                  <td colSpan={9} className="py-12 text-center text-ink-faint">
                    無符合條件的標的
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
  center,
}: {
  children: React.ReactNode;
  right?: boolean;
  center?: boolean;
}) {
  return (
    <th
      className={`px-3 py-3 font-medium first:pl-5 ${
        right ? "text-right" : center ? "text-center" : "text-left"
      }`}
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
}: {
  children: React.ReactNode;
  right?: boolean;
  mono?: boolean;
  dim?: boolean;
}) {
  return (
    <td
      className={`px-3 py-3 text-sm ${right ? "text-right" : ""} ${
        mono ? "font-mono tnum" : ""
      } ${dim ? "text-ink-dim" : "text-ink"}`}
    >
      {children}
    </td>
  );
}
