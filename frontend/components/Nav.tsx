"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "總覽" },
  { href: "/scanner", label: "選股" },
  { href: "/divergence", label: "主力背離" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-20 border-b border-line-soft bg-bg/70 backdrop-blur-xl">
      <div className="mx-auto flex max-w-6xl items-center gap-5 px-4 py-4 sm:gap-8 sm:px-6">
        <Link href="/" className="group flex items-baseline gap-2">
          <span className="font-display text-xl text-gold">Chip</span>
          <span className="text-sm font-medium tracking-[0.2em] text-ink uppercase">
            Terminal
          </span>
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          {NAV.map((n) => {
            const active =
              n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={`relative rounded-md px-3 py-1.5 transition-colors ${
                  active
                    ? "text-ink"
                    : "text-ink-dim hover:text-ink"
                }`}
              >
                {n.label}
                {active && (
                  <span className="absolute inset-x-3 -bottom-[17px] h-px bg-gold" />
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
