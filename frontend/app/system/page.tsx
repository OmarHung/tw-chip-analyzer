"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, SectionTitle, StatCard } from "@/components/Card";
import {
  api,
  type BackfillRequest,
  type OpsJob,
  type OpsStatusResponse,
  type OpsTickDay,
} from "@/lib/api";

/* 位元組 → 人類可讀(GB/MB/KB)。 */
function fmtBytes(v: number | null): string {
  if (v == null) return "—";
  if (v >= 1 << 30) return `${(v / (1 << 30)).toFixed(2)} GB`;
  if (v >= 1 << 20) return `${(v / (1 << 20)).toFixed(1)} MB`;
  if (v >= 1 << 10) return `${(v / (1 << 10)).toFixed(0)} KB`;
  return `${v} B`;
}

function fmtNum(v: number): string {
  return v.toLocaleString("zh-TW");
}

/* 配額用量色:非價格語意,純危險度。高=紅(緊)、中=金(留意)、低=灰。 */
function quotaTone(pct: number | null): { text: string; bar: string } {
  if (pct == null) return { text: "text-ink-dim", bar: "bg-ink-faint" };
  if (pct >= 90) return { text: "text-up", bar: "bg-up" };
  if (pct >= 70) return { text: "text-gold-bright", bar: "bg-gold-bright" };
  return { text: "text-gold", bar: "bg-gold" };
}

const DOW_ZH: Record<string, string> = { "mon-fri": "週一至週五" };

function fmtNextRun(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-TW", {
      timeZone: "Asia/Taipei",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      weekday: "short",
    });
  } catch {
    return iso;
  }
}

const SOURCE_ZH: Record<string, string> = {
  feature_daily: "特徵 feature_daily",
  daily_price: "日K daily_price",
  institutional_daily: "法人 institutional",
  margin_daily: "融資券 margin",
  tdcc_summary_weekly: "集保 TDCC",
  sbl_daily: "借券 SBL",
  market_daily: "大盤 market_daily",
  raw_tick: "逐筆 raw_tick",
};

const QUOTA_STOP = 95; // 對齊後端 intraday_batch.usage_stop_pct

export default function SystemPage() {
  const [data, setData] = useState<OpsStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 回補表單
  const [bfKind, setBfKind] = useState<"single" | "range">("single");
  const [bfDate, setBfDate] = useState("");
  const [bfMode, setBfMode] = useState<"eod" | "ticks">("eod");
  const [bfStart, setBfStart] = useState("");
  const [bfEnd, setBfEnd] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [bfError, setBfError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api
      .opsStatus()
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch(() => setError("無法連線後端 API"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // 首次載入後用最新資料日預填表單
  useEffect(() => {
    if (data && !bfDate) {
      const latest = data.coverage.sources.daily_price.max ?? "";
      setBfDate(latest);
      setBfEnd(latest);
      setBfStart(data.coverage.sources.feature_daily.min ?? "");
    }
  }, [data, bfDate]);

  const running = data?.job.state === "running";

  // 執行中每 3s 輪詢(靜默,不觸發整頁 loading)
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => {
      api.opsStatus().then(setData).catch(() => {});
    }, 3000);
    return () => clearInterval(id);
  }, [running]);

  const q = data?.quota;
  const tone = quotaTone(q?.used_pct ?? null);
  const sched = data?.schedule;
  const quotaFull =
    (q?.available && q.used_pct != null && q.used_pct >= QUOTA_STOP) ?? false;
  const ticksBlocked = bfMode === "ticks" && quotaFull;

  const submit = async () => {
    setSubmitting(true);
    setBfError(null);
    try {
      const body: BackfillRequest =
        bfKind === "single"
          ? { kind: "single", date: bfDate, mode: bfMode }
          : { kind: "range", start: bfStart, end: bfEnd };
      const res = await api.backfill(body);
      setData(res);
    } catch (e) {
      setBfError(e instanceof Error ? e.message : "觸發失敗");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-display text-4xl leading-none">
            系統<span className="text-gold">狀態</span>
          </h1>
          <p className="mt-2 font-mono text-xs tracking-wider text-ink-faint">
            Shioaji 配額 · 資料涵蓋 · 排程 · 回補{loading && " · 載入中"}
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="rounded-lg border border-line-soft bg-panel/50 px-4 py-2 font-mono text-xs tracking-wider text-ink-dim transition-colors hover:text-ink disabled:opacity-50"
        >
          重新整理
        </button>
      </div>

      {error ? (
        <div className="rounded-2xl border border-line-soft bg-panel/70 p-10 text-center text-ink-dim">
          {error}
        </div>
      ) : !data ? (
        <div className="rounded-2xl border border-line-soft bg-panel/70 p-10 text-center text-ink-faint">
          載入中…
        </div>
      ) : (
        <>
          {/* 任務狀態(執行中/剛完成才顯示) */}
          {data.job.state !== "idle" && <JobStatus job={data.job} />}

          {/* 資料回補 */}
          <Card>
            <SectionTitle>資料回補</SectionTitle>
            <div className="space-y-4">
              {/* 單日 / 區間 */}
              <div className="flex gap-1 rounded-lg border border-line-soft bg-panel-2/50 p-0.5 w-fit">
                {(["single", "range"] as const).map((k) => (
                  <button
                    key={k}
                    onClick={() => setBfKind(k)}
                    className={`rounded-md px-4 py-1.5 font-mono text-xs transition-colors ${
                      bfKind === k
                        ? "bg-gold/15 text-gold"
                        : "text-ink-dim hover:text-ink"
                    }`}
                  >
                    {k === "single" ? "單日" : "區間"}
                  </button>
                ))}
              </div>

              {bfKind === "single" ? (
                <div className="flex flex-wrap items-end gap-4">
                  <Field label="日期">
                    <DateInput value={bfDate} onChange={setBfDate} />
                  </Field>
                  <Field label="模式">
                    <div className="flex gap-1 rounded-lg border border-line-soft bg-panel-2/50 p-0.5">
                      {(
                        [
                          { k: "eod", label: "完整 EOD" },
                          { k: "ticks", label: "只逐筆" },
                        ] as const
                      ).map((m) => (
                        <button
                          key={m.k}
                          onClick={() => setBfMode(m.k)}
                          className={`rounded-md px-3 py-1.5 font-mono text-xs transition-colors ${
                            bfMode === m.k
                              ? "bg-gold/15 text-gold"
                              : "text-ink-dim hover:text-ink"
                          }`}
                        >
                          {m.label}
                        </button>
                      ))}
                    </div>
                  </Field>
                </div>
              ) : (
                <div className="flex flex-wrap items-end gap-4">
                  <Field label="起始日">
                    <DateInput value={bfStart} onChange={setBfStart} />
                  </Field>
                  <Field label="結束日">
                    <DateInput value={bfEnd} onChange={setBfEnd} />
                  </Field>
                </div>
              )}

              {/* 說明 / 守門提示 */}
              <p className="text-xs text-ink-faint">
                {bfKind === "single"
                  ? bfMode === "eod"
                    ? "完整 EOD:匯入行情/法人/融資/TDCC/TAIEX + 逐筆 + 重建特徵並落地分數。"
                    : "只逐筆:補當日 Shioaji 逐筆後重建特徵(其餘盤後資料不動)。"
                  : "區間回補只補日線/法人/融資 + 特徵(不含逐筆,避免燒穿配額);平日上限 90 天。"}
              </p>
              {ticksBlocked && (
                <p className="text-xs text-up">
                  配額已達 {q?.used_pct}%(≥{QUOTA_STOP}%),逐筆回補會立即中止 —
                  請等每日配額回補後再試,或改用「完整 EOD」。
                </p>
              )}
              {bfError && <p className="text-xs text-up">{bfError}</p>}

              <button
                onClick={submit}
                disabled={running || submitting || ticksBlocked}
                className="rounded-lg border border-gold/40 bg-gold/10 px-5 py-2 font-mono text-xs tracking-wider text-gold transition-colors hover:bg-gold/15 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {running ? "工作進行中…" : submitting ? "觸發中…" : "觸發回補"}
              </button>
            </div>
          </Card>

          {/* Shioaji 配額 */}
          <Card>
            <div className="mb-4 flex items-center justify-between">
              <SectionTitle>Shioaji 資料配額</SectionTitle>
              {q?.cached_age_sec != null && (
                <span className="font-mono text-[10px] text-ink-faint">
                  {q.cached_age_sec}s 前更新
                </span>
              )}
            </div>
            {!q?.available ? (
              <div className="text-sm text-ink-faint">
                配額資訊暫時無法取得(未設金鑰或連線失敗)。
              </div>
            ) : (
              <div className="space-y-3">
                <div className="flex items-baseline justify-between">
                  <span className={`font-mono text-4xl font-bold tnum ${tone.text}`}>
                    {q.used_pct != null ? `${q.used_pct}%` : "—"}
                  </span>
                  <span className="font-mono text-sm text-ink-dim tnum">
                    {fmtBytes(q.bytes)} / {fmtBytes(q.limit_bytes)}
                  </span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full bg-line-soft">
                  <div
                    className={`bar-fill h-full ${tone.bar}`}
                    style={{ width: `${Math.min(q.used_pct ?? 0, 100)}%` }}
                  />
                </div>
                {(q.used_pct ?? 0) >= 90 && (
                  <p className="text-xs text-up">
                    配額接近上限 — 逐筆匯入會在 {QUOTA_STOP}% 自動停止;等每日回補後再抓。
                  </p>
                )}
              </div>
            )}
          </Card>

          {/* 排程 + 涵蓋摘要 */}
          <div className="grid gap-4 sm:grid-cols-3">
            <StatCard
              label="下次 EOD"
              value={
                <span className="text-2xl">{fmtNextRun(sched?.next_run ?? null)}</span>
              }
              accent="text-gold"
              sub={
                sched
                  ? `${DOW_ZH[sched.eod.day_of_week] ?? sched.eod.day_of_week} ${String(
                      sched.eod.hour,
                    ).padStart(2, "0")}:${String(sched.eod.minute).padStart(2, "0")} · ${
                      sched.running ? "排程運行中" : sched.enabled ? "未啟動" : "已停用"
                    }`
                  : undefined
              }
            />
            <StatCard
              label="特徵交易日"
              value={data.coverage.sources.feature_daily.days}
              sub={`${data.coverage.sources.feature_daily.min ?? "—"} ~ ${
                data.coverage.sources.feature_daily.max ?? "—"
              }`}
            />
            <StatCard
              label="逐筆總筆數"
              value={fmtNum(data.coverage.row_counts.raw_tick)}
              sub={`涵蓋 ${data.coverage.sources.raw_tick.days} 個交易日`}
            />
          </div>

          {/* 資料源涵蓋 */}
          <Card>
            <SectionTitle>資料源涵蓋</SectionTitle>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-line-soft font-mono text-[10px] tracking-[0.12em] text-ink-faint uppercase">
                    <th className="py-2 pr-3 text-left font-medium">資料源</th>
                    <th className="px-3 py-2 text-right font-medium">交易日</th>
                    <th className="px-3 py-2 text-right font-medium">最早</th>
                    <th className="py-2 pl-3 text-right font-medium">最新</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.coverage.sources).map(([k, v]) => (
                    <tr key={k} className="border-b border-line-soft/50 last:border-0">
                      <td className="py-2.5 pr-3 text-sm text-ink">
                        {SOURCE_ZH[k] ?? k}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-sm tnum text-ink">
                        {v.days}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-sm tnum text-ink-dim">
                        {v.min ?? "—"}
                      </td>
                      <td className="py-2.5 pl-3 text-right font-mono text-sm tnum text-ink-dim">
                        {v.max ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* 逐筆每日灌檔數(診斷配額被砍) */}
          <Card>
            <SectionTitle>
              逐筆每日匯入(近 {data.coverage.tick_by_date.length} 日)
            </SectionTitle>
            {data.coverage.tick_by_date.length === 0 ? (
              <div className="text-sm text-ink-faint">尚無逐筆資料。</div>
            ) : (
              <div className="space-y-1.5">
                {data.coverage.tick_by_date.map((d) => (
                  <TickRow key={d.date} d={d} />
                ))}
                <p className="pt-2 text-xs text-ink-faint">
                  每天完整批次約 200 檔(依 config `intraday_batch.max_symbols`)。
                  某日檔數明顯偏少通常代表當天 Shioaji 配額已用盡而提早中止。
                </p>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

/* 回補任務狀態條。 */
function JobStatus({ job }: { job: OpsJob }) {
  const running = job.state === "running";
  const border =
    job.state === "error"
      ? "border-up/40"
      : running
        ? "border-gold/40"
        : "border-line-soft";
  const pct =
    job.progress && job.progress.total > 0
      ? Math.min((job.progress.done / job.progress.total) * 100, 100)
      : null;

  return (
    <div className={`rounded-2xl border ${border} bg-panel/70 p-5`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {running && (
            <span className="h-2 w-2 animate-pulse rounded-full bg-gold" />
          )}
          <span className="font-mono text-xs tracking-wider text-ink uppercase">
            {job.state === "running"
              ? "回補進行中"
              : job.state === "done"
                ? "回補完成"
                : job.state === "error"
                  ? "回補失敗"
                  : "閒置"}
          </span>
          {job.target && (
            <span className="font-mono text-xs text-ink-dim">· {job.target}</span>
          )}
        </div>
        {job.finished_at && !running && (
          <span className="font-mono text-[10px] text-ink-faint">
            {job.finished_at.slice(0, 19).replace("T", " ")}
          </span>
        )}
      </div>

      {job.step && (
        <div className="mt-2 font-mono text-xs text-ink-dim">{job.step}</div>
      )}

      {pct != null && (
        <div className="mt-3 space-y-1">
          <div className="h-2 overflow-hidden rounded-full bg-line-soft">
            <div className="h-full bg-gold" style={{ width: `${pct}%` }} />
          </div>
          <div className="text-right font-mono text-[10px] text-ink-faint tnum">
            {job.progress!.done}/{job.progress!.total}
            {job.progress!.usage_pct != null &&
              ` · 配額 ${job.progress!.usage_pct}%`}
          </div>
        </div>
      )}

      {job.result != null && !running && (
        <div className="mt-2 font-mono text-xs text-ink-dim">
          {renderResult(job.result)}
        </div>
      )}
      {job.error && <div className="mt-2 text-xs text-up">{job.error}</div>}
    </div>
  );
}

function renderResult(r: Record<string, unknown>): string {
  const t = r.ticks as
    | { fetched?: number; target?: number; failed?: number; usage_pct?: number | null; stopped?: boolean }
    | undefined;
  if (t && typeof t === "object" && "fetched" in t) {
    return `逐筆:成功 ${t.fetched ?? 0} / 目標 ${t.target ?? 0}(失敗 ${
      t.failed ?? 0
    }${t.stopped ? "，配額中止" : ""}${
      t.usage_pct != null ? `，配額 ${t.usage_pct}%` : ""
    })`;
  }
  if (typeof r.days === "number") {
    return `回補完成:${r.days} 個交易日(${String(r.range ?? "")})`;
  }
  return JSON.stringify(r);
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
        {label}
      </span>
      {children}
    </label>
  );
}

function DateInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type="date"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-lg border border-line-soft bg-panel-2/50 px-3 py-1.5 font-mono text-sm text-ink outline-none focus:border-gold/40"
    />
  );
}

/* 單日逐筆:檔數長條(以 200 為滿格基準)+ 筆數。 */
function TickRow({ d }: { d: OpsTickDay }) {
  const pct = Math.min((d.symbols / 200) * 100, 100);
  const low = d.symbols < 100;
  return (
    <div className="flex items-center gap-3">
      <span className="w-24 shrink-0 font-mono text-xs text-ink-dim tnum">
        {d.date}
      </span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-line-soft">
        <div
          className={`h-full ${low ? "bg-up" : "bg-gold"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span
        className={`w-12 shrink-0 text-right font-mono text-xs tnum ${
          low ? "text-up" : "text-ink"
        }`}
      >
        {d.symbols} 檔
      </span>
      <span className="hidden w-24 shrink-0 text-right font-mono text-xs text-ink-faint tnum sm:block">
        {fmtNum(d.ticks)} 筆
      </span>
    </div>
  );
}
