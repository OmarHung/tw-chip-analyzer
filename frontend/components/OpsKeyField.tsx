"use client";

/** 後端回 401 時出現的管理金鑰輸入框（值只存在本分頁 sessionStorage）。 */
export function OpsKeyField({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
        管理金鑰 OPS_API_KEY
      </span>
      <input
        type="password"
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="輸入後再操作一次"
        className="w-full max-w-sm rounded-lg border border-line-soft bg-panel-2/50 px-3 py-1.5 font-mono text-sm text-ink outline-none focus:border-gold/40"
      />
    </label>
  );
}
