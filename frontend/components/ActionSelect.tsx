"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { Action } from "@/lib/api";
import { ActionBadge } from "@/components/ActionBadge";
import { ACTION_LABEL } from "@/lib/format";

type Value = Action | "";

/** 動作篩選的自訂下拉選單（取代原生 select，貼合 Terminal Luxe）。 */
export function ActionSelect({
  value,
  options,
  onChange,
}: {
  value: Value;
  options: Value[];
  onChange: (v: Value) => void;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const id = useId();

  // 點擊外部 / Esc 關閉
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // 開啟時把 active 對齊目前值
  useEffect(() => {
    if (open) setActive(Math.max(0, options.indexOf(value)));
  }, [open, value, options]);

  const commit = (v: Value) => {
    onChange(v);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActive((i) => (i + 1) % options.length);
        break;
      case "ArrowUp":
        e.preventDefault();
        setActive((i) => (i - 1 + options.length) % options.length);
        break;
      case "Home":
        e.preventDefault();
        setActive(0);
        break;
      case "End":
        e.preventDefault();
        setActive(options.length - 1);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        commit(options[active]);
        break;
      case "Escape":
        e.preventDefault();
        setOpen(false);
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  };

  // active 選項捲入視野
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.children[active] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyDown}
        className={`flex min-w-[7.5rem] items-center justify-between gap-3 rounded-lg border bg-panel-2 px-3 py-1.5 text-sm text-ink transition-colors outline-none ${
          open ? "border-gold/50" : "border-line hover:border-line/80"
        }`}
      >
        <OptionLabel value={value} />
        <svg
          viewBox="0 0 12 12"
          className={`h-3 w-3 shrink-0 text-ink-faint transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <path d="M2.5 4.5 6 8l3.5-3.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <ul
          ref={listRef}
          id={`${id}-list`}
          role="listbox"
          aria-activedescendant={`${id}-opt-${active}`}
          tabIndex={-1}
          onKeyDown={onKeyDown}
          className="absolute right-0 z-30 mt-2 max-h-72 w-max min-w-full max-w-[calc(100vw-2rem)] overflow-auto rounded-xl border border-line bg-panel/95 p-1 shadow-[0_12px_40px_rgba(0,0,0,0.55)] backdrop-blur-xl"
        >
          {options.map((opt, i) => {
            const selected = opt === value;
            const isActive = i === active;
            return (
              <li
                key={opt || "all"}
                id={`${id}-opt-${i}`}
                role="option"
                aria-selected={selected}
                onMouseEnter={() => setActive(i)}
                onClick={() => commit(opt)}
                className={`flex cursor-pointer items-center justify-between gap-3 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-sm transition-colors ${
                  isActive ? "bg-white/[0.06]" : ""
                }`}
              >
                <OptionLabel value={opt} />
                {selected && (
                  <svg viewBox="0 0 14 14" className="h-3.5 w-3.5 shrink-0 text-gold" fill="none" stroke="currentColor" strokeWidth="1.75">
                    <path d="M2.5 7.5 6 11l5.5-7" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function OptionLabel({ value }: { value: Value }) {
  if (value === "")
    return <span className="font-mono text-sm tracking-wider whitespace-nowrap text-ink">全部</span>;
  return (
    <span className="flex items-center gap-2 whitespace-nowrap">
      <ActionBadge action={value} />
      <span className="text-sm text-ink-dim">{ACTION_LABEL[value]}</span>
    </span>
  );
}
