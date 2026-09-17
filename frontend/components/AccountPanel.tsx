"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/AuthContext";
import { Card, SectionTitle } from "@/components/Card";
import { api, type Role, type UserRow } from "@/lib/api";

const INPUT =
  "rounded-lg border border-line-soft bg-panel-2/50 px-3 py-1.5 font-mono text-sm text-ink outline-none focus:border-gold/40 disabled:opacity-40";
const BTN_GOLD =
  "rounded-lg border border-gold/40 bg-gold/10 px-3 py-1.5 font-mono text-xs text-gold transition-colors hover:bg-gold/15 disabled:cursor-not-allowed disabled:opacity-30";
const BTN_PLAIN =
  "rounded-md border border-line-soft px-3 py-1 font-mono text-[11px] text-ink-dim transition-colors hover:text-ink disabled:opacity-30";

function errText(e: unknown): string {
  return e instanceof Error ? e.message : "操作失敗";
}

/** 帳號相關設定。相容模式（尚未建立任何帳號）時不顯示——沒有帳號可管。 */
export function AccountPanel() {
  const me = useAuth();
  if (!me.authenticated) return null;
  return (
    <>
      <PasswordCard mustChange={me.must_change_password} />
      {me.role === "admin" && <UsersCard myUsername={me.username} />}
    </>
  );
}

function PasswordCard({ mustChange }: { mustChange: boolean }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (next !== confirm) {
      setError("兩次輸入的新密碼不一致");
      return;
    }
    try {
      await api.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      setNotice("密碼已更新，其他裝置的登入已全部失效");
    } catch (e) {
      setError(errText(e));
    }
  }

  return (
    <Card className={mustChange ? "border-gold/40" : ""}>
      <SectionTitle>我的密碼</SectionTitle>
      {mustChange && (
        <p className="mb-3 text-xs text-gold">
          目前使用的是管理者代設的密碼，請立即改成只有你知道的密碼。
        </p>
      )}
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <Field label="目前密碼" name="current-password" value={current} onChange={setCurrent} autoComplete="current-password" />
        <Field label="新密碼" name="new-password" value={next} onChange={setNext} autoComplete="new-password" />
        <Field label="確認新密碼" name="confirm-password" value={confirm} onChange={setConfirm} autoComplete="new-password" />
        <button type="submit" disabled={!current || !next} className={BTN_GOLD}>
          更新密碼
        </button>
      </form>
      {error && <p className="mt-3 text-xs text-up">{error}</p>}
      {notice && <p className="mt-3 text-xs text-ink-dim">{notice}</p>}
    </Card>
  );
}

function Field({
  label,
  value,
  onChange,
  autoComplete,
  name,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete?: string;
  name?: string;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
        {label}
      </span>
      <input
        type="password"
        name={name}
        autoComplete={autoComplete}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`w-44 ${INPUT}`}
      />
    </label>
  );
}

function UsersCard({ myUsername }: { myUsername: string }) {
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<Role>("viewer");
  const [resetting, setResetting] = useState<number | null>(null);
  const [resetValue, setResetValue] = useState("");

  const load = useCallback(() => {
    api
      .users()
      .then((d) => setUsers(d.users))
      .catch((e) => setError(errText(e)));
  }, []);

  useEffect(load, [load]);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      load();
    } catch (e) {
      setError(errText(e));
    }
  };

  return (
    <Card>
      <SectionTitle>帳號管理</SectionTitle>
      <p className="mb-4 text-xs text-ink-dim">
        admin 可修改設定、觸發工作與回補；viewer 只能檢視。停用帳號或重設密碼會立即使該
        帳號所有登入失效。
      </p>

      <div className="overflow-x-auto">
        <table className="w-full font-mono text-xs">
          <thead>
            <tr className="border-b border-line-soft text-[10px] tracking-[0.12em] text-ink-faint uppercase">
              <th className="py-2 pr-3 text-left font-medium">帳號</th>
              <th className="px-3 py-2 text-left font-medium">角色</th>
              <th className="px-3 py-2 text-left font-medium">狀態</th>
              <th className="px-3 py-2 text-left font-medium">最後登入</th>
              <th className="py-2 pl-3 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {(users ?? []).map((u) => (
              <tr key={u.id} className="border-b border-line-soft/60 text-ink-dim">
                <td className="py-2 pr-3 text-ink">
                  {u.username}
                  {u.username === myUsername && (
                    <span className="ml-2 text-[10px] text-ink-faint">（你）</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <select
                    value={u.role}
                    onChange={(e) =>
                      run(() => api.updateUser(u.id, { role: e.target.value as Role }))
                    }
                    className={INPUT}
                  >
                    <option value="admin">admin</option>
                    <option value="viewer">viewer</option>
                  </select>
                </td>
                <td className="px-3 py-2">{u.is_active ? "啟用" : "停用"}</td>
                <td className="px-3 py-2">
                  {u.last_login_at
                    ? new Date(u.last_login_at).toLocaleString("zh-TW")
                    : "—"}
                </td>
                <td className="py-2 pl-3">
                  <div className="flex flex-wrap justify-end gap-2">
                    <button
                      className={BTN_PLAIN}
                      onClick={() =>
                        run(() => api.updateUser(u.id, { is_active: !u.is_active }))
                      }
                    >
                      {u.is_active ? "停用" : "啟用"}
                    </button>
                    <button
                      className={BTN_PLAIN}
                      onClick={() => {
                        setResetting(resetting === u.id ? null : u.id);
                        setResetValue("");
                      }}
                    >
                      重設密碼
                    </button>
                    <button
                      className={BTN_PLAIN}
                      disabled={u.username === myUsername}
                      onClick={() => run(() => api.deleteUser(u.id))}
                    >
                      刪除
                    </button>
                  </div>
                  {resetting === u.id && (
                    <div className="mt-2 flex justify-end gap-2">
                      <input
                        type="password"
                        name={`reset-password-${u.id}`}
                        autoComplete="new-password"
                        placeholder="新密碼"
                        value={resetValue}
                        onChange={(e) => setResetValue(e.target.value)}
                        className={`w-44 ${INPUT}`}
                      />
                      <button
                        className={BTN_GOLD}
                        disabled={!resetValue}
                        onClick={() =>
                          run(async () => {
                            await api.resetUserPassword(u.id, resetValue);
                            setResetting(null);
                            setResetValue("");
                          })
                        }
                      >
                        送出
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <form
        className="mt-5 flex flex-wrap items-end gap-3 border-t border-line-soft pt-5"
        onSubmit={(e) => {
          e.preventDefault();
          run(async () => {
            await api.createUser({
              username: newName,
              password: newPassword,
              role: newRole,
            });
            setNewName("");
            setNewPassword("");
          });
        }}
      >
        <label className="flex flex-col gap-1.5">
          <span className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
            新帳號
          </span>
          <input
            name="new-username"
            value={newName}
            autoComplete="off"
            onChange={(e) => setNewName(e.target.value)}
            className={`w-44 ${INPUT}`}
          />
        </label>
        <Field label="密碼" name="new-user-password" value={newPassword} onChange={setNewPassword} autoComplete="new-password" />
        <label className="flex flex-col gap-1.5">
          <span className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
            角色
          </span>
          <select
            value={newRole}
            onChange={(e) => setNewRole(e.target.value as Role)}
            className={INPUT}
          >
            <option value="viewer">viewer</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <button type="submit" disabled={!newName || !newPassword} className={BTN_GOLD}>
          建立帳號
        </button>
      </form>

      {error && <p className="mt-3 text-xs text-up">{error}</p>}
    </Card>
  );
}
