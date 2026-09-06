"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ActionBadge } from "@/components/ActionBadge";
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
      <div>
        <h1 className="text-2xl font-bold">Scanner</h1>
        <p className="mt-1 text-sm text-slate-400">
          資料日期 {asOf ?? "—"}　·　{rows.length} 檔
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-4 rounded-xl border border-slate-800 bg-slate-900/60 p-4">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-slate-400">最低 Score</span>
          <input
            type="range"
            min={0}
            max={90}
            step={5}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            className="accent-emerald-500"
          />
          <span className="w-8 tabular-nums text-slate-200">{minScore}</span>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-slate-400">Action</span>
          <select
            value={action}
            onChange={(e) => setAction(e.target.value as Action | "")}
            className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-slate-100"
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
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-8 text-center text-slate-400">
          {error}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-slate-800">
          <table className="w-full text-sm">
            <thead className="bg-slate-900/80 text-slate-400">
              <tr>
                <Th>股票</Th>
                <Th right>價格</Th>
                <Th right>Chip</Th>
                <Th right>盤中</Th>
                <Th right>法人</Th>
                <Th right>TDCC</Th>
                <Th center>Action</Th>
                <Th right>成交金額</Th>
                <Th right>RR</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {rows.map((r) => (
                <tr key={r.symbol} className="hover:bg-slate-900/50">
                  <td className="px-3 py-2">
                    <Link
                      href={`/stocks/${r.symbol}`}
                      className="font-medium text-emerald-400 hover:underline"
                    >
                      {r.symbol}
                    </Link>
                  </td>
                  <Td right>{fmtPrice(r.price)}</Td>
                  <Td right>
                    <span className={`font-semibold ${scoreColor(r.chip_score)}`}>
                      {r.chip_score.toFixed(1)}
                    </span>
                  </Td>
                  <Td right>{r.intraday.toFixed(0)}</Td>
                  <Td right>{r.institutional.toFixed(0)}</Td>
                  <Td right>{r.holder.toFixed(0)}</Td>
                  <td className="px-3 py-2 text-center">
                    <ActionBadge action={r.action} />
                  </td>
                  <Td right>{fmtTurnover(r.turnover)}</Td>
                  <Td right>{r.rr?.toFixed(1) ?? "—"}</Td>
                </tr>
              ))}
              {!loading && rows.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-3 py-8 text-center text-slate-500">
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
      className={`px-3 py-2 font-medium ${
        right ? "text-right" : center ? "text-center" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}

function Td({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <td className={`px-3 py-2 tabular-nums ${right ? "text-right" : ""}`}>
      {children}
    </td>
  );
}
