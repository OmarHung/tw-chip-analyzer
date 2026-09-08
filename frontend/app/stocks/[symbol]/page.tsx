import Link from "next/link";
import { ActionBadge } from "@/components/ActionBadge";
import { Card, SectionTitle } from "@/components/Card";
import { Change } from "@/components/Change";
import { OrderFlowPanel } from "@/components/OrderFlowPanel";
import { ScoreBar, ScoreRing } from "@/components/ScoreBar";
import { StockCharts } from "@/components/StockCharts";
import { api, type FeaturesResponse } from "@/lib/api";
import { fmtPrice } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function StockDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ symbol: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const { symbol } = await params;
  const { tab } = await searchParams;
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

  // 還原後價格特徵：AVOID 股的 analysis 不輸出 MA/ATR，這裡直接取 feature_daily。
  // 缺特徵不該讓整頁掛掉（fail-soft，就不顯示這張卡）。
  let features: FeaturesResponse | null = null;
  try {
    features = await api.features(symbol);
  } catch {
    features = null;
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
            <MarketBadge market={data.market} />
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

      {/* 還原後價格結構（含公司行動還原因子） */}
      {features && <AdjustedPricePanel data={features} />}

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
        <StockCharts symbol={data.symbol} initialTab={tab} />
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

const TONE_CLASS = {
  up: "text-up",
  down: "text-down",
  neutral: "text-ink",
} as const;

function PriceStat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number | null;
  tone?: keyof typeof TONE_CLASS;
}) {
  return (
    <div className="rounded-xl border border-line-soft bg-panel-2/50 p-3 text-center">
      <div className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
        {label}
      </div>
      <div className={`mt-1 font-mono text-lg font-bold tnum ${TONE_CLASS[tone]}`}>
        {value != null ? fmtPrice(value) : "—"}
      </div>
    </div>
  );
}

const CA_LABEL: Record<string, string> = {
  權: "除權",
  息: "除息",
  權息: "除權息",
  面額: "面額變更",
  減資: "減資",
};

/** 還原後的價格結構。走勢圖與 chart 端點刻意保留原始價，MA/ATR 這類跨日統計則必須
 *  用還原價才不會被除權息／拆股的斷點污染——這張卡讓還原效果看得到、對得起來。 */
function AdjustedPricePanel({ data }: { data: FeaturesResponse }) {
  return (
    <Card className="reveal">
      <SectionTitle>還原後價格結構 · {data.date}</SectionTitle>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <PriceStat label="收盤" value={data.close} />
        <PriceStat label="MA20" value={data.ma20} />
        <PriceStat label="ATR14" value={data.atr14} />
        <PriceStat label="VWAP" value={data.vwap} />
        <PriceStat label="近 10 日低點" value={data.recent_swing_low} />
      </div>
      <div className="mt-4 flex flex-wrap gap-x-8 gap-y-2 border-t border-line-soft pt-4">
        <div className="flex items-center gap-2">
          <span className="text-sm text-ink-dim">距 MA20</span>
          <Change pct={data.close_vs_ma20_pct} />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-ink-dim">距 VWAP</span>
          <Change pct={data.close_vs_vwap_pct} />
        </div>
      </div>
      {data.actions.length > 0 && (
        <div className="mt-4 border-t border-line-soft pt-4">
          <div className="font-mono text-[10px] tracking-wider text-ink-faint uppercase">
            視窗內公司行動（已還原）
          </div>
          <ul className="mt-2 space-y-1.5">
            {data.actions.map((a) => (
              <li key={a.date} className="flex flex-wrap items-baseline gap-x-3 text-sm">
                <span className="font-mono tnum text-ink-dim">{a.date}</span>
                <span className="text-gold">{CA_LABEL[a.kind] ?? a.kind}</span>
                {a.prev_close != null && a.reference_price != null && (
                  <span className="font-mono tnum text-ink-dim">
                    {fmtPrice(a.prev_close)} → {fmtPrice(a.reference_price)}
                  </span>
                )}
                <span className="font-mono tnum text-ink-faint">
                  價 ×{a.adj_factor != null ? a.adj_factor.toFixed(4) : "—"} ／ 量 ×
                  {a.share_factor != null ? a.share_factor.toFixed(4) : "—"}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-ink-faint">
            上方數值皆為還原值；走勢圖仍顯示原始價（未還原），故除權息／拆股日會有斷點。
          </p>
        </div>
      )}
    </Card>
  );
}


/* 市場別徽章：資料源僅含上市(TWSE)與上櫃一般板(TPEx)，興櫃未匯入故不會出現。 */
const MARKET_ZH: Record<string, string> = { TWSE: "上市", TPEx: "上櫃" };

function MarketBadge({ market }: { market: string | null }) {
  if (!market) return null;
  return (
    <span className="rounded-md border border-line-soft bg-panel-2/50 px-2 py-0.5 font-mono text-[10px] tracking-wider text-ink-dim">
      {MARKET_ZH[market] ?? market}
    </span>
  );
}
