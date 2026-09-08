"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "總覽" },
  { href: "/scanner", label: "選股" },
  { href: "/divergence", label: "主力背離" },
  { href: "/validation", label: "驗證" },
  { href: "/system", label: "系統" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-20 border-b border-line-soft bg-bg/70 backdrop-blur-xl">
      <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-4 sm:gap-8 sm:px-6">
        <Link href="/" className="group flex shrink-0 items-baseline gap-2">
          <span className="font-display text-xl text-gold">Chip</span>
          <span className="hidden text-sm font-medium tracking-[0.2em] text-ink uppercase sm:inline">
            Terminal
          </span>
        </Link>
        <nav className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto text-sm [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {NAV.map((n) => {
            const active =
              n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={`relative shrink-0 rounded-md px-2.5 py-1.5 whitespace-nowrap transition-colors sm:px-3 ${
                  active
                    ? "bg-gold/10 text-ink sm:bg-transparent"
                    : "text-ink-dim hover:text-ink"
                }`}
              >
                {n.label}
                {active && (
                  <span className="absolute inset-x-3 -bottom-[17px] hidden h-px bg-gold sm:block" />
                )}
              </Link>
            );
          })}
        </nav>
        <span className="ml-auto hidden font-mono text-[11px] tracking-wider text-ink-faint sm:block">
          PHASE 1 · DAILY CHIP SCANNER
        </span>
      </div>
    </header>
  );
}
