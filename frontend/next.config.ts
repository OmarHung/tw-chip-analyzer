import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // dev server 預設只信任 localhost，用 127.0.0.1 開會擋掉 /_next 的 dev 資源與 HMR，
  // 結果是頁面畫得出來但 hydration 沒完成（打字不進 state、按鈕按不動）。
  // 認證用的 cookie 分主機名，開發時常需要跟 NEXT_PUBLIC_API_BASE 對齊成 127.0.0.1，
  // 所以兩個都列進來。只影響 dev，production build 不吃這個設定。
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
