// SSR 端的身分查詢。頁面殼層（app/(app)/layout.tsx）用它決定要不要導去登入頁。
import { ApiError, type Me } from "@/lib/api";
import { serverApi } from "@/lib/api.server";

const ANON: Me = {
  authenticated: false,
  auth_enabled: false,
  username: "anonymous",
  role: "viewer",
  kind: "open",
  must_change_password: false,
};

/**
 * 回傳目前身分。後端連不上時**不**把人擋在登入牆外（回相容模式的匿名身分），
 * 讓頁面自己顯示「無法連線後端」——否則後端一掛，使用者只會看到登入頁登不進去，
 * 誤以為是密碼錯。真正的授權判斷在後端，這裡只是 UI 導流。
 */
export async function getMe(): Promise<Me> {
  try {
    return await (await serverApi()).me();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      return { ...ANON, auth_enabled: true };
    }
    return ANON;
  }
}
