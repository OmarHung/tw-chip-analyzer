"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AccountPanel } from "@/components/AccountPanel";
import { useCanWrite } from "@/components/AuthContext";
import { Card, SectionTitle } from "@/components/Card";
import { OpsKeyField } from "@/components/OpsKeyField";
import { ReadOnlyNotice } from "@/components/ReadOnlyNotice";
import { TelegramSettings } from "@/components/TelegramSettings";
import { api, type SettingItem, type SettingsResponse } from "@/lib/api";
import { useOpsKey } from "@/lib/useOpsKey";

export default function SettingsPage() {
  const canWrite = useCanWrite();
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const ops = useOpsKey();

  const load = useCallback(() => {
    api
      .settings()
      .then((d) => {
        setData(d);
        setLoadError(null);
      })
      .catch(() => setLoadError("無法連線後端 API"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const groups = useMemo(() => {
    const out = new Map<string, SettingItem[]>();
    for (const item of data?.items ?? []) {
      out.set(item.group, [...(out.get(item.group) ?? []), item]);
    }
    return [...out.entries()];
  }, [data]);

  const act = async (fn: (key: string | undefined) => Promise<SettingsResponse>) => {
    setActionError(null);
    setNotice(null);
    try {
      setData(await ops.run(fn));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "操作失敗");
    }
  };

  const startRebuild = async () => {
    setActionError(null);
    try {
      const { run_id } = await ops.run((k) => api.startTask("rebuild_signals", {}, k));
      setNotice(`已啟動重建（#${run_id}），進度請至系統頁「工作」查看`);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "啟動失敗");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-4xl leading-none">
          門檻<span className="text-gold">設定</span>
        </h1>
        <p className="mt-2 font-mono text-xs tracking-wider text-ink-faint">
          YAML 為預設值 · 此頁修改存於 DB 覆寫 · 每次修改留存歷史
        </p>
      </div>

      {loadError && (
        <div className="rounded-2xl border border-line-soft bg-panel/70 p-10 text-center text-ink-dim">
          {loadError}
        </div>
      )}

      <ReadOnlyNotice />

      {/* 帳號／密碼不依賴門檻設定是否載入成功，獨立於 data 之外 */}
      <AccountPanel />

      {data && (
        <>
          {data.pending_rebuild && (
            <Card className="border-gold/40">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="space-y-1">
                  <div className="text-sm text-gold">歷史分數尚未依目前設定重建</div>
                  <div className="text-xs text-ink-dim">
                    signal_snapshot 有資料不是用目前「需重建」類設定（版本 {data.data_version}）產生，
                    驗證頁會混用新舊分數。全量重建約十幾分鐘，1 vCPU 主機執行期間網站會變慢；
                    重建結果屬樣本內，不是前瞻 OOS。
                  </div>
                </div>
                <button
                  onClick={startRebuild}
                  disabled={!canWrite}
                  className="rounded-lg border border-gold/40 bg-gold/10 px-5 py-2 font-mono text-xs tracking-wider text-gold transition-colors hover:bg-gold/15"
                >
                  全量重建分數
                </button>
              </div>
            </Card>
          )}

          {data.running_version !== data.data_version && (
            <p className="text-xs text-up">
              API 目前生效的設定版本（{data.running_version}）與 DB 不一致，請重新整理或重啟 API。
            </p>
          )}

          {(actionError || notice || ops.needKey) && (
            <Card>
              <div className="space-y-3">
                {ops.needKey && <OpsKeyField value={ops.draft} onChange={ops.setDraft} />}
                {actionError && <p className="text-xs text-up">{actionError}</p>}
                {notice && (
                  <p className="text-xs text-ink-dim">
                    {notice}{" "}
                    <Link href="/system" className="text-gold hover:underline">
                      前往系統頁
                    </Link>
                  </p>
                )}
              </div>
            </Card>
          )}

          {data.orphaned.length > 0 && (
            <Card>
              <SectionTitle>失效的覆寫</SectionTitle>
              <p className="mb-3 text-xs text-ink-dim">
                DB 有覆寫值，但 YAML／登錄表已無此設定（多半是設定改名），目前不生效。
              </p>
              <ul className="space-y-2">
                {data.orphaned.map((k) => (
                  <li key={k} className="flex items-center justify-between gap-3">
                    <span className="font-mono text-xs text-ink">{k}</span>
                    <button
                      onClick={() => act((key) => api.resetSetting(k, key))}
                      className="rounded-md border border-line-soft px-3 py-1 font-mono text-[11px] text-ink-dim hover:text-ink"
                    >
                      清除
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <TelegramSettings />

          {groups.map(([group, items]) => (
            <Card key={group}>
              <SectionTitle>{group}</SectionTitle>
              <div className="divide-y divide-line-soft">
                {items.map((item) => (
                  <SettingRow
                    key={`${item.key}:${item.effective}`}
                    item={item}
                    readOnly={!canWrite}
                    onSave={(value, unlock) =>
                      act((k) => api.updateSetting(item.key, value, unlock, k))
                    }
                    onReset={() => act((k) => api.resetSetting(item.key, k))}
                  />
                ))}
              </div>
            </Card>
          ))}

          <Card>
            <SectionTitle>修改歷史（最近 50 筆）</SectionTitle>
            {data.history.length === 0 ? (
              <p className="text-sm text-ink-faint">尚無修改</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full font-mono text-xs">
                  <thead>
                    <tr className="border-b border-line-soft text-[10px] tracking-[0.12em] text-ink-faint uppercase">
                      <th className="py-2 pr-3 text-left font-medium">時間</th>
                      <th className="px-3 py-2 text-left font-medium">設定</th>
                      <th className="px-3 py-2 text-right font-medium">舊值</th>
                      <th className="px-3 py-2 text-right font-medium">新值</th>
                      <th className="py-2 pl-3 text-left font-medium">來源</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.history.map((h, i) => (
                      <tr key={i} className="border-b border-line-soft/60 text-ink-dim">
                        <td className="py-2 pr-3 whitespace-nowrap">
                          {h.changed_at ? new Date(h.changed_at).toLocaleString("zh-TW") : "—"}
                        </td>
                        <td className="px-3 py-2 text-ink">{h.key}</td>
                        <td className="px-3 py-2 text-right tnum">{h.old ?? "預設"}</td>
                        <td className="px-3 py-2 text-right tnum">{h.new ?? "預設"}</td>
                        <td className="py-2 pl-3 text-ink-faint">{h.source}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

function SettingRow({
  item,
  onSave,
  onReset,
  readOnly = false,
}: {
  item: SettingItem;
  onSave: (value: number | string, unlock: boolean) => void;
  onReset: () => void;
  readOnly?: boolean;
}) {
  const [value, setValue] = useState(String(item.effective ?? ""));
  const [unlock, setUnlock] = useState(false);
  const dirty = value !== String(item.effective ?? "");
  const disabled = (item.locked && !unlock) || readOnly;

  const save = () => {
    if (item.kind === "choice") return onSave(value, unlock);
    const num = Number(value);
    if (value.trim() === "" || Number.isNaN(num)) return;
    onSave(num, unlock);
  };

  return (
    <div className="grid gap-3 py-3 sm:grid-cols-[1fr_auto] sm:items-center">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-ink">{item.label}</span>
          <EffectBadge effect={item.effect} />
          {item.override !== null && (
            <span className="rounded bg-gold/10 px-1.5 py-0.5 font-mono text-[10px] text-gold">
              已覆寫
            </span>
          )}
          {item.locked && (
            <span className="rounded border border-line-soft px-1.5 py-0.5 font-mono text-[10px] text-ink-dim">
              鎖定
            </span>
          )}
        </div>
        <div className="font-mono text-[11px] text-ink-faint">
          {item.key} · 預設 {String(item.default)}
          {item.min !== null && item.max !== null && ` · 範圍 ${item.min}～${item.max}`}
        </div>
        {item.help && <div className="text-xs text-ink-dim">{item.help}</div>}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {item.locked && (
          <label className="flex items-center gap-1.5 text-xs text-ink-dim">
            <input
              type="checkbox"
              checked={unlock}
              onChange={(e) => setUnlock(e.target.checked)}
            />
            解鎖（未經 OOS 驗證）
          </label>
        )}
        {item.kind === "choice" ? (
          <select
            value={value}
            disabled={disabled}
            onChange={(e) => setValue(e.target.value)}
            className="rounded-lg border border-line-soft bg-panel-2/50 px-2.5 py-1.5 font-mono text-sm text-ink outline-none focus:border-gold/40 disabled:opacity-40"
          >
            {item.choices.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        ) : (
          <input
            type="number"
            value={value}
            disabled={disabled}
            step={item.kind === "int" ? 1 : "any"}
            min={item.min ?? undefined}
            max={item.max ?? undefined}
            onChange={(e) => setValue(e.target.value)}
            className="w-32 rounded-lg border border-line-soft bg-panel-2/50 px-2.5 py-1.5 text-right font-mono text-sm tnum text-ink outline-none focus:border-gold/40 disabled:opacity-40"
          />
        )}
        <button
          onClick={save}
          disabled={!dirty || disabled}
          className="rounded-lg border border-gold/40 bg-gold/10 px-3 py-1.5 font-mono text-xs text-gold transition-colors hover:bg-gold/15 disabled:cursor-not-allowed disabled:opacity-30"
        >
          儲存
        </button>
        <button
          onClick={onReset}
          disabled={item.override === null || readOnly}
          className="rounded-lg border border-line-soft px-3 py-1.5 font-mono text-xs text-ink-dim transition-colors hover:text-ink disabled:cursor-not-allowed disabled:opacity-30"
        >
          重設
        </button>
      </div>
    </div>
  );
}

function EffectBadge({ effect }: { effect: "live" | "rebuild" }) {
  return effect === "live" ? (
    <span className="rounded border border-line-soft px-1.5 py-0.5 font-mono text-[10px] text-ink-dim">
      即時生效
    </span>
  ) : (
    <span className="rounded bg-gold/10 px-1.5 py-0.5 font-mono text-[10px] text-gold">
      需重建
    </span>
  );
}
