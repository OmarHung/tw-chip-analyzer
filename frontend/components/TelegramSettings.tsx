"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Card, SectionTitle } from "@/components/Card";
import { OpsKeyField } from "@/components/OpsKeyField";
import {
  api,
  type NotifySource,
  type TelegramChat,
  type TelegramPatch,
  type TelegramSettings as Settings,
} from "@/lib/api";
import { useOpsKey } from "@/lib/useOpsKey";

const SOURCE_LABEL: Record<NotifySource, string> = {
  db: "此頁設定",
  env: ".env",
  yaml: "YAML 預設",
  unset: "未設定",
};

// 台股語意：紅＝多（BUY），綠＝空／迴避（AVOID）
const ACTION_COLOR: Record<string, string> = {
  BUY: "text-up",
  AVOID: "text-down",
};

interface Draft {
  enabled: boolean;
  token: string; // 空字串＝不變更
  chatId: string;
  actions: string[];
  maxItems: string;
  maxReasons: string;
  minTurnover: string;
}

const toDraft = (s: Settings): Draft => ({
  enabled: s.enabled,
  token: "",
  chatId: s.chat_id,
  actions: s.actions,
  maxItems: String(s.max_items),
  maxReasons: String(s.max_reasons),
  minTurnover: String(s.min_turnover),
});

/** 只送有變動的欄位；chat id 清空且原本是此頁設定 → 送 null 回到 .env。 */
function buildPatch(s: Settings, d: Draft): TelegramPatch {
  const patch: TelegramPatch = {};
  if (d.enabled !== s.enabled) patch.enabled = d.enabled;
  if (d.token.trim()) patch.bot_token = d.token.trim();
  if (d.chatId.trim() !== s.chat_id) patch.chat_id = d.chatId.trim() || null;
  if (d.actions.join() !== s.actions.join()) patch.actions = d.actions;
  const nums: [keyof TelegramPatch, string, number][] = [
    ["max_items", d.maxItems, s.max_items],
    ["max_reasons", d.maxReasons, s.max_reasons],
    ["min_turnover", d.minTurnover, s.min_turnover],
  ];
  for (const [key, raw, current] of nums) {
    const n = Number(raw);
    if (raw.trim() !== "" && !Number.isNaN(n) && n !== current) {
      (patch as Record<string, number>)[key] = n;
    }
  }
  return patch;
}

/** 設定頁「Telegram 推播」：EOD 後推新進 BUY / AVOID。寫入需 OPS 金鑰。 */
export function TelegramSettings() {
  const [data, setData] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [chats, setChats] = useState<TelegramChat[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ops = useOpsKey();

  const apply = useCallback((s: Settings) => {
    setData(s);
    setDraft(toDraft(s));
  }, []);

  useEffect(() => {
    api
      .telegramSettings()
      .then(apply)
      .catch(() => setError("無法載入 Telegram 設定"));
  }, [apply]);

  const act = async (fn: () => Promise<void>) => {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失敗");
    } finally {
      setBusy(false);
    }
  };

  if (!data || !draft) {
    return error ? (
      <Card>
        <SectionTitle>Telegram 推播</SectionTitle>
        <p className="text-xs text-up">{error}</p>
      </Card>
    ) : null;
  }

  const patch = buildPatch(data, draft);
  const dirty = Object.keys(patch).length > 0;
  const set = (p: Partial<Draft>) => setDraft({ ...draft, ...p });

  const save = () =>
    act(async () => {
      apply(await ops.run((k) => api.updateTelegram(patch, k)));
      setNotice("已儲存，下次 EOD 推播生效");
    });

  const clearToken = () =>
    act(async () => {
      apply(await ops.run((k) => api.updateTelegram({ bot_token: null }, k)));
      setNotice("已清除此頁的 Bot token（改用 .env，若有）");
    });

  const sendTest = () =>
    act(async () => {
      await ops.run((k) => api.testTelegram(k));
      setNotice("測試訊息已送出，請到 Telegram 確認");
    });

  const detect = () =>
    act(async () => {
      const { chats } = await ops.run((k) => api.detectTelegramChats(draft.token.trim(), k));
      setChats(chats);
      if (chats.length === 0) setNotice("沒有找到對話：請先在 Telegram 對 bot 傳一則訊息（群組需先把 bot 加入）");
    });

  const toggleAction = (a: string) =>
    set({
      actions: draft.actions.includes(a)
        ? draft.actions.filter((x) => x !== a)
        : [...draft.actions, a],
    });

  const inputCls =
    "rounded-lg border border-line-soft bg-panel-2/50 px-2.5 py-1.5 font-mono text-sm text-ink outline-none focus:border-gold/40";
  const btnGold =
    "rounded-lg border border-gold/40 bg-gold/10 px-3 py-1.5 font-mono text-xs text-gold transition-colors hover:bg-gold/15 disabled:cursor-not-allowed disabled:opacity-30";
  const btnPlain =
    "rounded-lg border border-line-soft px-3 py-1.5 font-mono text-xs text-ink-dim transition-colors hover:text-ink disabled:cursor-not-allowed disabled:opacity-30";

  return (
    <Card>
      <SectionTitle>Telegram 推播</SectionTitle>
      <p className="mb-4 text-xs text-ink-dim">
        每交易日 EOD 分數落地後，推送「新進」的訊號（與前一筆快照的建議不同）。
        推播是通知，不是交易依據——§28 成功標準尚未達成。
      </p>

      <div className="divide-y divide-line-soft">
        <Row label="啟用自動推播" source={data.sources.enabled}>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => set({ enabled: e.target.checked })}
            />
            {draft.enabled ? "啟用" : "停用"}
          </label>
        </Row>

        <Row
          label="Bot token"
          source={data.sources.bot_token}
          help={
            data.token_set
              ? `目前：${data.token_masked}（輸入新值才會覆蓋）`
              : "向 @BotFather 建立 bot 後取得"
          }
        >
          <input
            type="password"
            autoComplete="off"
            value={draft.token}
            placeholder={data.token_set ? "••••••（不變更）" : "123456789:AA…"}
            onChange={(e) => set({ token: e.target.value })}
            className={`${inputCls} w-72`}
          />
          <button
            onClick={clearToken}
            disabled={busy || data.sources.bot_token !== "db"}
            className={btnPlain}
          >
            清除
          </button>
        </Row>

        <Row
          label="Chat ID"
          source={data.sources.chat_id}
          help="個人對話為正數，群組／頻道為負數；不知道可按「偵測」"
        >
          <input
            value={draft.chatId}
            placeholder="例如 123456789"
            onChange={(e) => set({ chatId: e.target.value })}
            className={`${inputCls} w-48`}
          />
          <button onClick={detect} disabled={busy} className={btnPlain}>
            偵測
          </button>
        </Row>

        {chats && chats.length > 0 && (
          <div className="space-y-1.5 py-3">
            <div className="text-xs text-ink-dim">最近傳訊給 bot 的對話（點選帶入）：</div>
            {chats.map((c) => (
              <button
                key={c.chat_id}
                onClick={() => {
                  set({ chatId: c.chat_id });
                  setChats(null);
                }}
                className="flex w-full items-center justify-between gap-3 rounded-lg border border-line-soft px-3 py-1.5 text-left text-sm text-ink transition-colors hover:border-gold/40"
              >
                <span>{c.title}</span>
                <span className="font-mono text-xs text-ink-faint">
                  {c.type} · {c.chat_id}
                </span>
              </button>
            ))}
          </div>
        )}

        <Row label="推播類型" source={data.sources.actions}>
          <div className="flex flex-wrap gap-3">
            {data.allowed_actions.map((a) => (
              <label key={a} className="flex items-center gap-1.5 font-mono text-xs">
                <input
                  type="checkbox"
                  checked={draft.actions.includes(a)}
                  onChange={() => toggleAction(a)}
                />
                <span className={ACTION_COLOR[a] ?? "text-ink-dim"}>{a}</span>
              </label>
            ))}
          </div>
        </Row>

        <Row
          label="每類最多列出"
          source={data.sources.max_items}
          help="其餘只報檔數（AVOID 單日新進可達上百檔）"
        >
          <NumberInput value={draft.maxItems} min={1} max={200} onChange={(v) => set({ maxItems: v })} />
        </Row>
        <Row label="每檔原因條數" source={data.sources.max_reasons}>
          <NumberInput value={draft.maxReasons} min={0} max={10} onChange={(v) => set({ maxReasons: v })} />
        </Row>
        <Row
          label="最低成交金額（TWD）"
          source={data.sources.min_turnover}
          help="低於此值不列；0＝不過濾"
        >
          <NumberInput value={draft.minTurnover} min={0} onChange={(v) => set({ minTurnover: v })} wide />
        </Row>
      </div>

      <div className="mt-4 space-y-3">
        {ops.needKey && <OpsKeyField value={ops.draft} onChange={ops.setDraft} />}
        {error && <p className="text-xs text-up">{error}</p>}
        {notice && <p className="text-xs text-ink-dim">{notice}</p>}
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={save} disabled={busy || !dirty || draft.actions.length === 0} className={btnGold}>
            儲存
          </button>
          <button
            onClick={() => setDraft(toDraft(data))}
            disabled={busy || !dirty}
            className={btnPlain}
          >
            還原
          </button>
          <button
            onClick={sendTest}
            disabled={busy || dirty || !data.token_set || !data.chat_id}
            className={btnPlain}
            title={dirty ? "請先儲存" : undefined}
          >
            傳送測試訊息
          </button>
          {data.updated_at && (
            <span className="ml-auto font-mono text-[11px] text-ink-faint">
              最後修改 {new Date(data.updated_at).toLocaleString("zh-TW")}
            </span>
          )}
        </div>
      </div>
    </Card>
  );
}

function Row({
  label,
  source,
  help,
  children,
}: {
  label: string;
  source: NotifySource;
  help?: string;
  children: ReactNode;
}) {
  return (
    <div className="grid gap-3 py-3 sm:grid-cols-[1fr_auto] sm:items-center">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-ink">{label}</span>
          <span
            className={
              source === "db"
                ? "rounded bg-gold/10 px-1.5 py-0.5 font-mono text-[10px] text-gold"
                : "rounded border border-line-soft px-1.5 py-0.5 font-mono text-[10px] text-ink-dim"
            }
          >
            {SOURCE_LABEL[source]}
          </span>
        </div>
        {help && <div className="text-xs text-ink-dim">{help}</div>}
      </div>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

function NumberInput({
  value,
  min,
  max,
  wide,
  onChange,
}: {
  value: string;
  min?: number;
  max?: number;
  wide?: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      step={1}
      onChange={(e) => onChange(e.target.value)}
      className={`${wide ? "w-40" : "w-24"} rounded-lg border border-line-soft bg-panel-2/50 px-2.5 py-1.5 text-right font-mono text-sm tnum text-ink outline-none focus:border-gold/40`}
    />
  );
}
