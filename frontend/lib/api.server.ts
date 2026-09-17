// SSR（server component）專用的 API client：把使用者的 session cookie 轉發給後端，
// 讓「誰在看」與「後端授權對象」是同一個人，而不是共用一把服務金鑰。
//
// 這個檔案匯入 next/headers，只能在 server component 使用；client component 一律
// 用 lib/api.ts 的 `api`（瀏覽器自己會帶 cookie）。
import { cookies } from "next/headers";
import { createApi } from "@/lib/api";

export async function serverApi() {
  const jar = await cookies();
  const cookie = jar.toString();
  return createApi(cookie ? { Cookie: cookie } : {});
}
