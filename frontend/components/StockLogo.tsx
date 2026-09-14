"use client";

import { useState } from "react";
import { API_BASE } from "@/lib/api";

/* 個股商標：後端代理的公司網域 favicon（台股沒有免費的正式 logo 來源，見 app/api/logos.py）。
   查無時後端回 404 → onError 退回名稱首字徽章，不顯示破圖或預設地球。 */

type Props = {
  symbol: string;
  name: string;
  hasWebsite: boolean; // 無網址時直接顯示徽章，不發注定 404 的請求
  size: number;
  className?: string;
};

export function StockLogo({ symbol, name, hasWebsite, size, className = "" }: Props) {
  const [failed, setFailed] = useState(false);
  const box = `inline-flex shrink-0 items-center justify-center overflow-hidden rounded-[3px] bg-white/90 ${className}`;

  if (!hasWebsite || failed) {
    return (
      <span
        aria-hidden
        className={`${box} font-sans font-medium leading-none text-black/75`}
        style={{ width: size, height: size, fontSize: Math.round(size * 0.62) }}
      >
        {name.trim().charAt(0) || "?"}
      </span>
    );
  }

  return (
    <span aria-hidden className={box} style={{ width: size, height: size }}>
      {/* favicon 只有幾十 px 且後端已快取，走 next/image 優化器反而多一趟轉發 */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`${API_BASE}/api/stocks/${encodeURIComponent(symbol)}/logo`}
        alt=""
        width={size}
        height={size}
        loading="lazy"
        className="h-full w-full object-contain"
        onError={() => setFailed(true)}
      />
    </span>
  );
}
