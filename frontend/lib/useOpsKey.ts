"use client";

import { useCallback, useState } from "react";
import { ApiError } from "@/lib/api";

const STORAGE = "twchip.opsKey";

/**
 * ops 管理金鑰（後端設 OPS_API_KEY 時寫入型操作需要）。
 * 只存本分頁 sessionStorage，不進前端 bundle。遇 401 清掉舊值並要求輸入。
 */
export function useOpsKey() {
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
          setNeedKey(true);
        }
        throw e;
      }
    },
    [draft],
  );

  return { draft, setDraft, needKey, run };
}
