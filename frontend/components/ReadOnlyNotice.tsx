"use client";

import { useAuth } from "@/components/AuthContext";

/** viewer 身分時的唯讀提示。相容模式（尚未建立帳號）不顯示——那時寫入靠金鑰/本機。 */
export function ReadOnlyNotice() {
  const me = useAuth();
  if (!me.auth_enabled || me.role === "admin") return null;
  return (
    <p className="rounded-lg border border-line-soft bg-panel/50 px-3 py-2 text-xs text-ink-dim">
      目前以 viewer（唯讀）身分登入：可檢視全部資料，但無法修改設定、觸發工作或回補。
    </p>
  );
}
