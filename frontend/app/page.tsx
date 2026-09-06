import Link from "next/link";
import { ActionBadge } from "@/components/ActionBadge";
import { Card, SectionTitle, StatCard } from "@/components/Card";
import { Change } from "@/components/Change";
import { api, type Action } from "@/lib/api";
import { dirColor, scoreColor } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  let data;
  try {
    data = await api.dashboard();
  } catch {
    return <ErrorState message="無法連線後端 API，請確認 uvicorn 執行中。" />;
  }
  if (data.total === 0) {
    return <ErrorState message="尚無特徵資料。請先執行盤後匯入 job。" />;
  }

  const m = data.market;
  const regimeUp = (m?.trend_score ?? 0) >= 0.15;
  const regimeDown = (m?.trend_score ?? 0) <= -0.15;

  return (
    <div className="space-y-10">
      {/* Hero 市場列 */}
      <section className="reveal" style={{ animationDelay: "0ms" }}>
        <div className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="font-display text-4xl leading-none">
              市場<span className="italic text-gold">概覽</span>
            </h1>
            <p className="mt-2 font-mono text-xs tracking-wider text-ink-faint">
              {data.as_of} · 全市場 {data.total} 檔
            </p>
          </div>
          <Link
            href="/scanner"
            className="group flex items-center gap-2 rounded-full border border-gold/30 bg-gold/10 px-5 py-2 text-sm font-medium text-gold transition-colors hover:bg-gold/20"
          >
            開始選股
            <span className="transition-transform group-hover:translate-x-0.5">→</span>
          </Link>
        </div>

        {m && (
          <Card className="relative overflow-hidden">
            <div
              className={`pointer-events-none absolute -right-16 -top-16 h-48 w-48 rounded-full blur-3xl ${
                regimeUp ? "bg-up/10" : regimeDown ? "bg-down/10" : "bg-gold/5"
              }`}
            />
            <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
              <div>
                <Label>TAIEX 加權指數</Label>
                <div className="mt-2 font-mono text-3xl font-bold tnum">
                  {m.taiex_close?.toLocaleString("zh-TW") ?? "—"}
                </div>
              </div>
              <div>
                <Label>大盤氣氛</Label>
                <div className="mt-2 flex items-center gap-2">
                  <span
                    className={`font-display text-3xl italic ${dirColor(m.trend_score)}`}
                  >
                    {m.regime}
                  </span>
                  {m.trend_score != null && (
                    <span className="font-mono text-sm tnum text-ink-faint">
                      {m.trend_score >= 0 ? "+" : ""}
                      {m.trend_score.toFixed(2)}
                    </span>
                  )}
                </div>
              </div>
              <div>
                <Label>均線 MA20 / MA60</Label>
                <div className="mt-2 font-mono text-sm tnum text-ink-dim">
                  {m.taiex_ma20?.toLocaleString("zh-TW") ?? "—"}
                  <span className="mx-1 text-ink-faint">/</span>
                  {m.taiex_ma60?.toLocaleString("zh-TW") ?? "—"}
                </div>
              </div>
              <div>
                <Label>漲 / 跌家數</Label>
                <div className="mt-2 flex items-center gap-3">
                  <span className="font-mono text-2xl font-bold tnum text-up">
                    {m.advancers ?? "—"}
                  </span>
                  <BreadthBar adv={m.advancers ?? 0} dec={m.decliners ?? 0} />
                  <span className="font-mono text-2xl font-bold tnum text-down">
                    {m.decliners ?? "—"}
                  </span>
                </div>
              </div>
            </div>
          </Card>
        )}
      </section>

      {/* 統計卡 */}
      <section
        className="reveal grid grid-cols-2 gap-4 md:grid-cols-4"
        style={{ animationDelay: "80ms" }}
      >
        <StatCard label="掃描標的" value={data.total} />
        <StatCard label="買進候選" value={data.buy_candidates} accent="text-gold-bright" />
        <StatCard label="觀察候選" value={data.watch_candidates} accent="text-gold" />
        <StatCard
          label="平均籌碼分數"
          value={data.avg_chip_score.toFixed(1)}
          accent={scoreColor(data.avg_chip_score)}
        />
      </section>

      {/* 訊號分布 + Top */}
      <div className="grid gap-6 lg:grid-cols-5">
        <section
          className="reveal lg:col-span-2"
          style={{ animationDelay: "160ms" }}
        >
          <Card>
            <SectionTitle>訊號分布</SectionTitle>
            <div className="space-y-3">
              {Object.entries(data.action_counts)
                .sort((a, b) => b[1] - a[1])
                .map(([action, count]) => (
                  <div key={action} className="flex items-center gap-3">
                    <div className="w-14">
                      <ActionBadge action={action as Action} />
                    </div>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-line-soft">
                      <div
                        className="bar-fill h-full rounded-full bg-gold-dim"
                        style={{ width: `${(count / data.total) * 100}%` }}
                      />
                    </div>
                    <span className="w-10 text-right font-mono text-sm tnum text-ink-dim">
                      {count}
                    </span>
                  </div>
                ))}
            </div>
          </Card>
        </section>

        <section
          className="reveal lg:col-span-3"
          style={{ animationDelay: "240ms" }}
        >
          <Card className="p-0">
            <div className="px-6 pt-6">
              <SectionTitle>籌碼強度 Top 10</SectionTitle>
            </div>
            <ul>
              {data.top.map((t, i) => (
                <li key={t.symbol}>
                  <Link
                    href={`/stocks/${t.symbol}`}
                    className="flex items-center gap-4 border-t border-line-soft px-6 py-3 transition-colors hover:bg-white/[0.02]"
                  >
                    <span className="w-4 font-mono text-xs text-ink-faint">
                      {i + 1}
                    </span>
                    <span className="font-mono text-sm font-medium text-ink">
                      {t.symbol}
                    </span>
                    <span className="truncate text-sm text-ink-dim">{t.name}</span>
                    <Change pct={t.change_pct} className="ml-auto text-xs" />
                    <span
                      className={`w-12 text-right font-mono text-sm font-bold tnum ${scoreColor(t.chip_score)}`}
                    >
                      {t.chip_score.toFixed(1)}
                    </span>
                    <ActionBadge action={t.action} />
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        </section>
      </div>

      <p className="text-[11px] leading-relaxed text-ink-faint">
        盤中(intraday)分項於 Phase 1 尚無即時資料源，暫不計入加權；產業趨勢待接入。
        價格漲跌以台股慣例顯示（<span className="text-up">紅漲</span> /{" "}
        <span className="text-down">綠跌</span>）。
      </p>
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="font-mono text-[10px] tracking-[0.15em] text-ink-faint uppercase">
      {children}
    </div>
  );
}

function BreadthBar({ adv, dec }: { adv: number; dec: number }) {
  const total = adv + dec || 1;
  return (
    <div className="flex h-1.5 flex-1 overflow-hidden rounded-full bg-line-soft">
      <div className="bar-fill bg-up" style={{ width: `${(adv / total) * 100}%` }} />
      <div className="bar-fill bg-down" style={{ width: `${(dec / total) * 100}%` }} />
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-2xl border border-line-soft bg-panel/70 p-10 text-center text-ink-dim">
      {message}
    </div>
  );
}
