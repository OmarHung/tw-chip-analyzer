"use client";

import { createContext, useContext } from "react";
import type { Me } from "@/lib/api";

const ANON: Me = {
  authenticated: false,
  auth_enabled: false,
  username: "anonymous",
  role: "viewer",
  kind: "open",
  must_change_password: false,
};

const Ctx = createContext<Me>(ANON);

/** 由 SSR 殼層注入目前身分，client 元件據此決定按鈕是否可用（後端仍會再驗一次）。 */
export function AuthProvider({ me, children }: { me: Me; children: React.ReactNode }) {
  return <Ctx.Provider value={me}>{children}</Ctx.Provider>;
}

export function useAuth(): Me {
  return useContext(Ctx);
}

/** 是否可執行寫入操作：已登入的 admin，或尚未建立帳號的相容模式（後端仍要求金鑰/本機）。 */
export function useCanWrite(): boolean {
  const me = useAuth();
  return me.role === "admin" || !me.auth_enabled;
}
