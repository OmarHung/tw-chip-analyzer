"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useCanWrite } from "@/components/AuthContext";
import { Card, SectionTitle } from "@/components/Card";
import { OpsKeyField } from "@/components/OpsKeyField";
import {
  api,
  type TaskParam,
  type TaskRun,
  type TaskSpec,
  type TasksResponse,
  type TaskStatus,
} from "@/lib/api";
import { useOpsKey } from "@/lib/useOpsKey";

const POLL_MS = 2000;
// 距底部多少 px 內視為「在底部」（使用者捲回底部即恢復跟隨）
const FOLLOW_THRESHOLD_PX = 24;

const STATUS_LABEL: Record<TaskStatus, string> = {
  running: "執行中",
  succeeded: "成功",
  failed: "失敗",
  cancelled: "已取消",
  interrupted: "中斷",
};

/** 系統頁「工作」：觸發白名單腳本（子行程）、看輸出、取消。 */
export function TaskPanel({ busy }: { busy: boolean }) {
  const canWrite = useCanWrite();
  const [data, setData] = useState<TasksResponse | null>(null);
  const [selected, setSelected] = useState<string>("rebuild_signals");
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [viewRun, setViewRun] = useState<TaskRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 輸出跟隨最新（IDE 終端機式）：開啟時新輸出自動捲到底；使用者往上捲即暫停
  const [follow, setFollow] = useState(true);
  const outputRef = useRef<HTMLPreElement>(null);
  const ops = useOpsKey();

  const refresh = useCallback(() => {
    api.tasks().then(setData).catch(() => setError("無法載入工作清單"));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // 正在看的工作若仍在執行，輪詢輸出；結束後刷新清單
  const viewId = viewRun?.id;
  const viewRunning = viewRun?.status === "running";
  useEffect(() => {
    if (viewId === undefined || !viewRunning) return;
    const id = setInterval(() => {
      api
        .taskRun(viewId)
        .then((r) => {
          setViewRun(r);
          if (r.status !== "running") refresh();
        })
        .catch(() => {});
    }, POLL_MS);
    return () => clearInterval(id);
  }, [viewId, viewRunning, refresh]);

  const output = viewRun?.output;
  useEffect(() => {
    const el = outputRef.current;
    if (follow && el) el.scrollTop = el.scrollHeight;
  }, [output, follow]);

  const onOutputScroll = () => {
    const el = outputRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= FOLLOW_THRESHOLD_PX;
    if (atBottom !== follow) setFollow(atBottom);
  };

  const spec = data?.tasks.find((t) => t.id === selected);

  const start = async () => {
    if (!spec) return;
    if (
      spec.rebuilds_scores &&
      !window.confirm("會覆寫 feature_daily 與 signal_snapshot，執行期間網站會變慢。確定執行？")
    )
      return;
    setError(null);
    try {
      const { run_id } = await ops.run((k) => api.startTask(spec.id, params, k));
      setViewRun(await api.taskRun(run_id));
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "啟動失敗");
    }
  };

  const cancel = async (runId: number) => {
    setError(null);
    try {
      await ops.run((k) => api.cancelTask(runId, k));
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消失敗");
    }
  };

  const open = async (runId: number) => {
    try {
      setViewRun(await api.taskRun(runId));
    } catch {
      setError("無法載入輸出");
    }
  };

  if (!data) {
    return (
      <Card>
        <SectionTitle>工作（腳本）</SectionTitle>
        <p className="text-sm text-ink-faint">{error ?? "載入中…"}</p>
      </Card>
    );
  }

  const groups: [string, TaskSpec[]][] = [
    ["維運", data.tasks.filter((t) => t.category === "maintenance")],
    ["研究報告", data.tasks.filter((t) => t.category === "research")],
  ];

  return (
    <Card>
      <SectionTitle>工作（腳本）</SectionTitle>
      <div className="space-y-5">
        <p className="text-xs text-ink-dim">
          以子行程執行白名單腳本，與 EOD／回補共用同一把鎖。工作執行中若到了 EOD 排程時間，
          <span className="text-up">當日 EOD 會延後重試</span>（每 15 分鐘再試一次，最晚到 18:00；
          晚間融資券／借券補抓同理，最晚到 23:30），請盡量避開平日 16:00 前後與 20:00 之後。
        </p>

        <div className="flex flex-wrap gap-4">
          {groups.map(([label, list]) => (
            <div key={label} className="space-y-1.5">
              <div className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
                {label}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {list.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => {
                      setSelected(t.id);
                      setParams({});
                    }}
                    className={`rounded-md border px-3 py-1.5 font-mono text-xs transition-colors ${
                      selected === t.id
                        ? "border-gold/40 bg-gold/15 text-gold"
                        : "border-line-soft text-ink-dim hover:text-ink"
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>

        {spec && (
          <div className="space-y-3 rounded-xl border border-line-soft bg-panel-2/30 p-4">
            {spec.help && <p className="text-xs text-ink-dim">{spec.help}</p>}
            {spec.params.length > 0 && (
              <div className="flex flex-wrap gap-4">
                {spec.params.map((p) => (
                  <ParamInput
                    key={p.name}
                    param={p}
                    value={params[p.name]}
                    onChange={(v) => setParams((prev) => ({ ...prev, [p.name]: v }))}
                  />
                ))}
              </div>
            )}
            {ops.needKey && <OpsKeyField value={ops.draft} onChange={ops.setDraft} />}
            {error && <p className="text-xs text-up">{error}</p>}
            <button
              onClick={start}
              disabled={busy || !canWrite}
              className="rounded-lg border border-gold/40 bg-gold/10 px-5 py-2 font-mono text-xs tracking-wider text-gold transition-colors hover:bg-gold/15 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? "已有工作進行中" : `執行「${spec.label}」`}
            </button>
          </div>
        )}

        {viewRun && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-mono text-xs text-ink">
                #{viewRun.id} {labelOf(data, viewRun.task_id)} ·{" "}
                <StatusText status={viewRun.status} />
                {viewRun.exit_code !== null && ` · exit ${viewRun.exit_code}`}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setFollow((f) => !f)}
                  title="新輸出時自動捲到最底（往上捲會暫停）"
                  aria-pressed={follow}
                  className={`rounded-md border px-3 py-1 font-mono text-[11px] transition-colors ${
                    follow
                      ? "border-gold/40 bg-gold/10 text-gold"
                      : "border-line-soft text-ink-dim hover:text-ink"
                  }`}
                >
                  ↓ 跟隨最新{follow ? "" : "（已暫停）"}
                </button>
                {viewRun.status === "running" && (
                  <button
                    onClick={() => cancel(viewRun.id)}
                    className="rounded-md border border-line-soft px-3 py-1 font-mono text-[11px] text-up hover:bg-up/10"
                  >
                    取消
                  </button>
                )}
                <button
                  onClick={() => setViewRun(null)}
                  className="rounded-md border border-line-soft px-3 py-1 font-mono text-[11px] text-ink-dim hover:text-ink"
                >
                  關閉
                </button>
              </div>
            </div>
            <pre
              ref={outputRef}
              onScroll={onOutputScroll}
              className="max-h-96 overflow-auto rounded-lg border border-line-soft bg-bg/60 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-ink-dim">
              {viewRun.output || "（尚無輸出）"}
            </pre>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="border-b border-line-soft text-[10px] tracking-[0.12em] text-ink-faint uppercase">
                <th className="py-2 pr-3 text-left font-medium">#</th>
                <th className="px-3 py-2 text-left font-medium">工作</th>
                <th className="px-3 py-2 text-left font-medium">狀態</th>
                <th className="px-3 py-2 text-left font-medium">開始</th>
                <th className="py-2 pl-3 text-right font-medium" />
              </tr>
            </thead>
            <tbody>
              {data.runs.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-3 text-ink-faint">
                    尚無執行紀錄
                  </td>
                </tr>
              )}
              {data.runs.map((r) => (
                <tr key={r.id} className="border-b border-line-soft/60 text-ink-dim">
                  <td className="py-2 pr-3 tnum">{r.id}</td>
                  <td className="px-3 py-2 text-ink">{labelOf(data, r.task_id)}</td>
                  <td className="px-3 py-2">
                    <StatusText status={r.status} />
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    {r.started_at ? new Date(r.started_at).toLocaleString("zh-TW") : "—"}
                  </td>
                  <td className="py-2 pl-3 text-right">
                    <button onClick={() => open(r.id)} className="text-gold hover:underline">
                      輸出
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}

function labelOf(data: TasksResponse, taskId: string): string {
  return data.tasks.find((t) => t.id === taskId)?.label ?? taskId;
}

function StatusText({ status }: { status: TaskStatus }) {
  const tone =
    status === "succeeded"
      ? "text-ink"
      : status === "running"
        ? "text-gold"
        : "text-up";
  return <span className={tone}>{STATUS_LABEL[status]}</span>;
}

function ParamInput({
  param,
  value,
  onChange,
}: {
  param: TaskParam;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const input =
    "rounded-lg border border-line-soft bg-panel-2/50 px-3 py-1.5 font-mono text-sm text-ink outline-none focus:border-gold/40";
  const label = `${param.label}${param.required ? " *" : ""}`;

  if (param.kind === "bool") {
    return (
      <label className="flex items-center gap-2 self-end pb-1.5 text-xs text-ink-dim">
        <input
          type="checkbox"
          checked={value === true}
          onChange={(e) => onChange(e.target.checked)}
        />
        {label}
      </label>
    );
  }

  return (
    <label className="flex flex-col gap-1.5" title={param.help}>
      <span className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
        {label}
      </span>
      {param.kind === "choice" ? (
        <select
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value || undefined)}
          className={input}
        >
          <option value="">（預設）</option>
          {param.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : (
        <input
          type={param.kind === "date" ? "date" : "number"}
          value={(value as string | number | undefined) ?? ""}
          min={param.min ?? undefined}
          max={param.max ?? undefined}
          step={param.kind === "int" ? 1 : "any"}
          placeholder="（預設）"
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") return onChange(undefined);
            onChange(param.kind === "date" ? raw : Number(raw));
          }}
          className={`${input} ${param.kind === "date" ? "" : "w-32 text-right tnum"}`}
        />
      )}
    </label>
  );
}
