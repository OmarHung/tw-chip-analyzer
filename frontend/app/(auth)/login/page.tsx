"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";

/** 登入頁。沒有導覽列（尚未通過認證），成功後回總覽；需改密碼者直接送到設定頁。 */
export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [hasUsers, setHasUsers] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .me()
      .then((me) => {
        if (!alive) return;
        if (me.authenticated || !me.auth_enabled) router.replace("/");
      })
      .catch(() => {});
    api
      .authState()
      .then((s) => alive && setHasUsers(s.has_users))
      .catch(() => alive && setHasUsers(null));
    return () => {
      alive = false;
    };
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!username || !password) {
      setError("請輸入帳號與密碼");
      return;
    }
    setBusy(true);
    try {
      const me = await api.login(username, password);
      router.replace(me.must_change_password ? "/settings" : "/");
      router.refresh();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "無法連線後端 API，請確認服務執行中。",
      );
      setBusy(false);
    }
  }

  return (
    <div className="relative z-10 flex min-h-full flex-1 items-center justify-center px-4 py-16">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="flex items-baseline justify-center gap-2">
            <span className="font-display text-3xl text-gold">Chip</span>
            <span className="text-sm font-medium tracking-[0.2em] text-ink uppercase">
              Terminal
            </span>
          </div>
          <p className="mt-2 font-mono text-[11px] tracking-wider text-ink-faint uppercase">
            台股籌碼分析 · 需登入
          </p>
        </div>

        <form
          onSubmit={submit}
          className="space-y-4 rounded-2xl border border-line-soft bg-panel/60 p-6 backdrop-blur-xl"
        >
          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
              帳號
            </span>
            <input
              autoFocus
              id="username"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg border border-line-soft bg-panel-2/50 px-3 py-2 font-mono text-sm text-ink outline-none focus:border-gold/40"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
              密碼
            </span>
            <input
              type="password"
              id="password"
              name="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-line-soft bg-panel-2/50 px-3 py-2 font-mono text-sm text-ink outline-none focus:border-gold/40"
            />
          </label>

          {error && (
            <p className="rounded-lg border border-up/30 bg-up/10 px-3 py-2 text-xs text-up">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-gold/90 px-4 py-2 text-sm font-medium text-bg transition-colors hover:bg-gold disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? "登入中…" : "登入"}
          </button>

          {hasUsers === false && (
            <p className="rounded-lg border border-line-soft bg-panel-2/40 px-3 py-2 text-[11px] leading-relaxed text-ink-dim">
              尚未建立任何帳號，系統目前處於相容模式（讀取端點未受保護）。請在主機上執行：
              <code className="mt-1 block font-mono text-[10px] text-gold">
                python -m scripts.manage_users create &lt;帳號&gt; --role admin
              </code>
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
