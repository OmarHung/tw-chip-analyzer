import type { Metadata } from "next";
import { Fraunces, JetBrains_Mono, Noto_Sans_TC } from "next/font/google";
import "./globals.css";
import { Nav } from "@/components/Nav";

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

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="zh-Hant"
      className={`${fraunces.variable} ${jbmono.variable} ${notoTC.variable} h-full antialiased`}
    >
      <body className="grain min-h-full flex flex-col">
        <div className="relative z-10 flex min-h-full flex-col">
          <Nav />
          <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-10">
            {children}
          </main>
          <footer className="border-t border-line-soft px-4 py-5 text-center text-[11px] tracking-widest text-ink-faint uppercase sm:px-6">
            僅供分析與研究 · 非投資建議
          </footer>
        </div>
      </body>
    </html>
  );
}
