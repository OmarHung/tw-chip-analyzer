import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "台股籌碼分析",
  description: "台股籌碼分析與進出場建議系統",
};

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/scanner", label: "Scanner" },
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="zh-Hant" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-slate-950 text-slate-100">
        <header className="border-b border-slate-800 bg-slate-900/50 backdrop-blur">
          <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
            <Link href="/" className="text-sm font-bold tracking-tight">
              <span className="text-emerald-400">籌碼</span>分析
            </Link>
            <nav className="flex gap-1 text-sm">
              {NAV.map((n) => (
                <Link
                  key={n.href}
                  href={n.href}
                  className="rounded-md px-3 py-1.5 text-slate-300 hover:bg-slate-800 hover:text-white"
                >
                  {n.label}
                </Link>
              ))}
            </nav>
            <span className="ml-auto text-xs text-slate-500">
              Phase 1 · Daily Chip Scanner
            </span>
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
          {children}
        </main>
        <footer className="border-t border-slate-800 px-6 py-4 text-center text-xs text-slate-600">
          僅供分析與研究，非投資建議。
        </footer>
      </body>
    </html>
  );
}
