import type { Metadata } from "next";
import { Fraunces, JetBrains_Mono, Noto_Sans_TC } from "next/font/google";
import "./globals.css";

const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
  weight: ["400", "500", "600"],
});
const jbmono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jbmono",
  weight: ["400", "500", "700"],
});
const notoTC = Noto_Sans_TC({
  subsets: ["latin"],
  variable: "--font-tc",
  weight: ["300", "400", "500", "700"],
});

export const metadata: Metadata = {
  title: "籌碼分析 · Chip Terminal",
  description: "台股籌碼分析與進出場建議系統",
};

/** 只負責 html/body 與字體。導覽列與登入守門在 app/(app)/layout.tsx。 */
export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="zh-Hant"
      className={`${fraunces.variable} ${jbmono.variable} ${notoTC.variable} h-full antialiased`}
    >
      <body className="grain min-h-full flex flex-col">{children}</body>
    </html>
  );
}
