"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ActionBadge } from "@/components/ActionBadge";
import { ActionSelect } from "@/components/ActionSelect";
import { Change } from "@/components/Change";
import { api, type Action, type ScannerRow } from "@/lib/api";
import { fmtPrice, fmtTurnover, scoreColor } from "@/lib/format";

const ACTIONS: (Action | "")[] = ["", "BUY", "WATCH", "HOLD", "REDUCE", "EXIT", "AVOID"];

export default function ScannerPage() {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [minScore, setMinScore] = useState(0);
  const [action, setAction] = useState<Action | "">("");
  const [rows, setRows] = useState<ScannerRow[]>([]);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 查詢輸入 debounce，避免每個按鍵都打 API
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => clearTimeout(id);
  }, [query]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .scanner({
        q: debouncedQuery || undefined,
        min_score: minScore,
        action: action || undefined,
        limit: 200,
      })
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
  }, [minScore, action, debouncedQuery]);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-display text-4xl leading-none">
            選<span className="text-gold">股</span>
          </h1>
          <p className="mt-2 font-mono text-xs tracking-wider text-ink-faint">
            {asOf ?? "—"} · {rows.length} 檔{loading && " · 載入中"}
          </p>
        </div>
      </div>

      {/* 篩選 */}
      <div className="flex flex-wrap items-center gap-x-8 gap-y-4 rounded-2xl border border-line-soft bg-panel/70 px-4 py-4 sm:px-6">
        <div className="relative w-full sm:w-64">
          <svg
            viewBox="0 0 16 16"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <circle cx="7" cy="7" r="4.5" />
            <path d="m11 11 3 3" strokeLinecap="round" />
          </svg>
          <input
            type="search"
            inputMode="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜尋代號 / 名稱"
            className="w-full rounded-lg border border-line bg-panel-2 py-1.5 pl-9 pr-8 text-sm text-ink placeholder:text-ink-faint outline-none transition-colors focus:border-gold/50"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label="清除搜尋"
              className="absolute right-2 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-full text-ink-faint transition-colors hover:bg-white/10 hover:text-ink"
            >
              <svg viewBox="0 0 12 12" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="m3 3 6 6M9 3l-6 6" strokeLinecap="round" />
              </svg>
            </button>
          )}
        </div>
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
        <div className="flex items-center gap-3 text-sm">
          <span className="font-mono text-[11px] tracking-wider text-ink-faint uppercase">
            動作
          </span>
          <ActionSelect value={action} options={ACTIONS} onChange={setAction} />
        </div>
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
                <Th right hideSm>法人</Th>
                <Th right hideSm>TDCC</Th>
                <Th center>動作</Th>
                <Th right hideSm>成交額</Th>
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
                  <td className="py-3 pl-4 pr-2 sm:pl-5 sm:pr-3">
                    <Link href={`/stocks/${r.symbol}?tab=flows`} className="group flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2">
                      <span className="font-mono text-sm font-medium text-ink group-hover:text-gold">
                        {r.symbol}
                      </span>
                      <span className="max-w-[7rem] truncate text-xs text-ink-dim sm:max-w-none sm:text-sm">{r.name}</span>
                    </Link>
                  </td>
                  <Td right mono>{fmtPrice(r.price)}</Td>
                  <td className="px-2 py-3 text-right sm:px-3">
                    <Change pct={r.change_pct} className="text-sm" />
                  </td>
                  <Td right>
                    <span className={`font-mono font-bold tnum ${scoreColor(r.chip_score)}`}>
                      {r.chip_score.toFixed(1)}
                    </span>
                  </Td>
                  <Td right mono dim hideSm>{r.institutional.toFixed(0)}</Td>
                  <Td right mono dim hideSm>{r.holder.toFixed(0)}</Td>
                  <td className="px-2 py-3 text-center sm:px-3">
                    <ActionBadge action={r.action} />
                  </td>
                  <Td right mono dim hideSm>{fmtTurnover(r.turnover)}</Td>
                  <Td right mono dim>{r.rr?.toFixed(1) ?? "—"}</Td>
                </tr>
              ))}
              {!loading && rows.length === 0 && (
                <tr>
                  <td colSpan={9} className="py-12 text-center text-ink-faint">
                    {debouncedQuery
                      ? `找不到符合「${debouncedQuery}」的標的`
                      : "無符合條件的標的"}
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
  hideSm,
}: {
  children: React.ReactNode;
  right?: boolean;
  center?: boolean;
  hideSm?: boolean;
}) {
  return (
    <th
      className={`whitespace-nowrap px-2 py-3 font-medium first:pl-4 sm:px-3 sm:first:pl-5 ${
        right ? "text-right" : center ? "text-center" : "text-left"
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
