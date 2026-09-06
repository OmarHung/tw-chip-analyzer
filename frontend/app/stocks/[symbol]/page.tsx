import Link from "next/link";
import { ActionBadge } from "@/components/ActionBadge";
import { Card, SectionTitle } from "@/components/Card";
import { Change } from "@/components/Change";
import { OrderFlowPanel } from "@/components/OrderFlowPanel";
import { ScoreBar, ScoreRing } from "@/components/ScoreBar";
import { StockCharts } from "@/components/StockCharts";
import { api } from "@/lib/api";
import { fmtPrice } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function StockDetailPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  let data;
  try {
    data = await api.analysis(symbol);
  } catch {
    return (
      <div className="rounded-2xl border border-line-soft bg-panel/70 p-10 text-center text-ink-dim">
        找不到 {symbol} 的分析資料。
        <div className="mt-4">
          <Link href="/scanner" className="text-gold hover:underline">
            ← 回選股
          </Link>
        </div>
      </div>
    );
  }

  const { scores, risk } = data;

  return (
    <div className="space-y-8">
      <Link
        href="/scanner"
        className="inline-flex items-center gap-1 font-mono text-xs tracking-wider text-ink-faint uppercase transition-colors hover:text-ink"
      >
        ← 回選股
      </Link>

      {/* 標頭 */}
      <section className="reveal flex flex-wrap items-center gap-x-8 gap-y-4">
        <div>
          <div className="flex items-baseline gap-3">
            <h1 className="font-mono text-3xl font-bold text-ink">{data.symbol}</h1>
            <span className="font-display text-2xl text-ink-dim">
              {data.name}
            </span>
          </div>
          <div className="mt-2 flex items-baseline gap-3">
            <span className="font-mono text-2xl tnum text-ink">
              {fmtPrice(data.price)}
            </span>
            <Change pct={data.change_pct} className="text-lg" />
            <ActionBadge action={data.action} showZh />
          </div>
        </div>
        <div className="ml-auto flex items-center gap-4">
          <ScoreRing score={data.chip_score} />
        </div>
      </section>

      <div className="grid gap-6 md:grid-cols-2">
        {/* 分數拆解 */}
        <Card className="reveal" >
          <SectionTitle>分數拆解</SectionTitle>
          <div className="space-y-5">
            <ScoreBar label="盤中 Intraday" score={scores.intraday} delay={0} hint="綜合分數未計入；實際盤中資金流見下方面板" />
            <ScoreBar label="法人 Institutional" score={scores.institutional} delay={80} />
            <ScoreBar label="集保 TDCC" score={scores.holder} delay={160} />
            <ScoreBar label="大盤 Market" score={scores.market} delay={240} />
          </div>
        </Card>

        {/* 風險報酬 */}
        <Card className="reveal">
          <SectionTitle>風險 / 報酬計畫</SectionTitle>
          <div className="space-y-4">
            <RiskRow label="進場區間">
              {data.entry
                ? `${fmtPrice(data.entry.low)} – ${fmtPrice(data.entry.high)}`
                : "—"}
            </RiskRow>
            <div className="grid grid-cols-3 gap-3">
              <PriceStat label="停損" value={risk.stop_loss} tone="down" />
              <PriceStat label="TP1" value={risk.tp1} tone="up" />
              <PriceStat label="TP2" value={risk.tp2} tone="up" />
            </div>
            <div className="flex items-center justify-between border-t border-line-soft pt-4">
              <span className="text-sm text-ink-dim">風險報酬比 RR</span>
              <span className="font-mono text-2xl font-bold tnum text-gold">
                {risk.rr != null ? risk.rr.toFixed(2) : "—"}
              </span>
            </div>
          </div>
        </Card>
      </div>

      {/* 訊號原因 */}
      <Card className="reveal">
        <SectionTitle>訊號原因</SectionTitle>
        {data.reasons.length ? (
          <ul className="grid gap-2 sm:grid-cols-2">
            {data.reasons.map((r, i) => (
              <li key={i} className="flex gap-2.5 text-sm text-ink">
                <span className="mt-0.5 text-gold">◆</span>
                {r}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-faint">無顯著訊號</p>
        )}
      </Card>

      {/* 盤中資金流（逐筆計算的 order flow） */}
      <div className="reveal">
        <OrderFlowPanel symbol={data.symbol} />
      </div>

      {/* 走勢圖 + 當日逐筆明細 */}
      <Card className="reveal">
        <StockCharts symbol={data.symbol} />
      </Card>
    </div>
  );
}

function RiskRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-ink-dim">{label}</span>
      <span className="font-mono tnum text-ink">{children}</span>
    </div>
  );
}

function PriceStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | null;
  tone: "up" | "down";
}) {
  return (
    <div className="rounded-xl border border-line-soft bg-panel-2/50 p-3 text-center">
      <div className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
        {label}
      </div>
      <div
        className={`mt-1 font-mono text-lg font-bold tnum ${
          tone === "up" ? "text-up" : "text-down"
        }`}
      >
        {value != null ? fmtPrice(value) : "—"}
      </div>
    </div>
  );
}
