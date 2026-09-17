"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { useAuth } from "@/components/AuthContext";
import { ApiError } from "@/lib/api";

const STORAGE = "twchip.opsKey";

/**
 * 包住寫入型操作，處理兩種「沒權限」：
 *
 * - **已啟用帳號**：401＝session 過期 → 導回登入頁；403＝已登入但非 admin，
 *   由呼叫端把後端訊息顯示出來（不需要金鑰）。
 * - **相容模式**（尚未建立任何帳號）：401＝需要 OPS_API_KEY → 顯示金鑰輸入框。
 *   金鑰只存本分頁 sessionStorage，不進前端 bundle。
 */
export function useOpsKey() {
  const me = useAuth();
  const router = useRouter();
  const [draft, setDraft] = useState("");
  const [needKey, setNeedKey] = useState(false);

  const run = useCallback(
    async <T,>(fn: (key: string | undefined) => Promise<T>): Promise<T> => {
      const key = draft || sessionStorage.getItem(STORAGE) || undefined;
      try {
        const out = await fn(key);
        if (key) sessionStorage.setItem(STORAGE, key);
        setNeedKey(false);
        return out;
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          sessionStorage.removeItem(STORAGE);
          if (me.auth_enabled) {
            router.replace("/login");
          } else {
            setNeedKey(true);
          }
        }
        throw e;
      }
    },
    [draft, me.auth_enabled, router],
  );

  return { draft, setDraft, needKey, run };
}
