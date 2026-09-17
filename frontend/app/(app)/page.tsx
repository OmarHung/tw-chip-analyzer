import Link from "next/link";
import { ActionBadge } from "@/components/ActionBadge";
import { Card, SectionTitle } from "@/components/Card";
import { Change } from "@/components/Change";
import { IndustryHeatmap } from "@/components/heatmap/IndustryHeatmap";
import { MarketTreemap } from "@/components/heatmap/MarketTreemap";
import { ApiTimeoutError, type Action, type DivergenceScanRow } from "@/lib/api";
import { serverApi } from "@/lib/api.server";
import { dirColor, scoreColor } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  // SSR 走轉發使用者 cookie 的 client（見 lib/api.server.ts）
  const api = await serverApi();
  let data;
  try {
    data = await api.dashboard();
  } catch (e) {
    // 逾時與連不上要分開講：前者多半是後端正在跑重建/回補，重整就會好；
    // 後者才需要去看 uvicorn。逾時若不在這裡攔下，整頁會死在 nginx 的 504。
    return (
      <ErrorState
        message={
          e instanceof ApiTimeoutError
            ? "後端回應逾時，可能正在重建特徵或執行回補。請稍候重新整理。"
            : "無法連線後端 API，請確認 uvicorn 執行中。"
        }
      />
    );
  }
  if (data.total === 0) {
    return <ErrorState message="尚無特徵資料。請先執行盤後匯入 job。" />;
  }

  // 今日主力背離快報（掃描失敗不應讓首頁掛掉）
  let bullish: DivergenceScanRow[] = [];
  let bearish: DivergenceScanRow[] = [];
  try {
    const [b, s] = await Promise.all([
      api.divergenceScan({ status: "bullish_div", limit: 5 }),
      api.divergenceScan({ status: "bearish_div", limit: 5 }),
    ]);
    bullish = b.rows;
    bearish = s.rows;
  } catch {
    // 忽略：快報為附加資訊
  }

  const m = data.market;
  const regimeUp = (m?.trend_score ?? 0) >= 0.15;
  const regimeDown = (m?.trend_score ?? 0) <= -0.15;
  // 大盤脈絡的兩個衍生值（由 dashboard 既有欄位算，不另外打 API）
  const vsMa20 =
    m?.taiex_close != null && m.taiex_ma20
      ? (m.taiex_close - m.taiex_ma20) / m.taiex_ma20
      : null;
  const breadth = (m?.advancers ?? 0) + (m?.decliners ?? 0);
  const advPct = breadth > 0 ? ((m?.advancers ?? 0) / breadth) * 100 : null;

  return (
    <div className="space-y-10">
      {/* Hero 市場列 */}
      <section className="reveal" style={{ animationDelay: "0ms" }}>
        <div className="mb-6 flex items-end justify-between">
          <div>
            <h1 className="font-display text-4xl leading-none">
              市場<span className="text-gold">概覽</span>
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
            {/* 兩區:左「報價」(大字,主角)、右「脈絡」(次級,同一基線),中間 1px 分隔。
                五格等重時眼睛不知道先看哪 —— 分區後階層自己會說話。 */}
            <div className="relative grid gap-y-8 lg:grid-cols-[auto_1px_minmax(0,1fr)] lg:gap-x-10">
              <div className="flex flex-wrap gap-x-12 gap-y-6">
                <Quote
                  label="TAIEX 加權指數"
                  value={m.taiex_close}
                  pts={m.taiex_change}
                  pct={m.taiex_change_pct}
                />
                <Quote
                  label={`台指期${m.futures ? ` ${m.futures.contract_month}` : ""}`}
                  value={m.futures?.close ?? null}
                  pts={m.futures?.change ?? null}
                  pct={m.futures?.change_pct ?? null}
                />
              </div>

              <div className="hidden bg-line-soft lg:block" />

              <div className="flex flex-wrap items-start gap-x-8 gap-y-6">
                <div className="shrink-0">
                  <Label>大盤氣氛</Label>
                  <div className="mt-3 flex h-7 items-center leading-none">
                    <span className={`font-display text-2xl ${dirColor(m.trend_score)}`}>
                      {m.regime}
                    </span>
                  </div>
                  <SubNote>
                    {m.trend_score != null
                      ? `趨勢 ${m.trend_score >= 0 ? "+" : ""}${m.trend_score.toFixed(2)}`
                      : "趨勢未知"}
                  </SubNote>
                </div>
                <div className="shrink-0">
                  <Label>均線 MA20 / MA60</Label>
                  <div className="mt-3 flex h-7 items-center font-mono text-sm tnum leading-none whitespace-nowrap text-ink-dim">
                    {m.taiex_ma20?.toLocaleString("zh-TW") ?? "—"}
                    <span className="mx-1 text-ink-faint">/</span>
                    {m.taiex_ma60?.toLocaleString("zh-TW") ?? "—"}
                  </div>
                  <SubNote>
                    收盤 <Change pct={vsMa20} className="text-xs" /> vs MA20
                  </SubNote>
                </div>
                <div className="min-w-[180px] flex-1">
                  <Label>漲 / 跌家數</Label>
                  <div className="mt-3 flex h-7 items-center gap-3 leading-none">
                    <span className="font-mono text-xl font-bold tnum text-up">
                      {m.advancers ?? "—"}
                    </span>
                    <BreadthBar adv={m.advancers ?? 0} dec={m.decliners ?? 0} />
                    <span className="font-mono text-xl font-bold tnum text-down">
                      {m.decliners ?? "—"}
                    </span>
                  </div>
                  <SubNote>
                    {advPct != null ? `上漲佔 ${advPct.toFixed(1)}%` : "—"}
                  </SubNote>
                </div>
              </div>
            </div>
          </Card>
        )}
      </section>

      {/* 掃描統計 + 訊號分布同卡（與 Hero 同構：左主右輔 + 1px 分隔）。
          分成兩張卡時,四個數字那張會被較高的訊號分布撐開成一片留白;
          併成一張,兩區高度自然相當,整頁也只剩「寬卡」一種節奏。 */}
      <section className="reveal" style={{ animationDelay: "80ms" }}>
        <Card>
          <div className="grid gap-y-8 lg:grid-cols-[minmax(0,0.8fr)_1px_minmax(0,1.2fr)] lg:gap-x-10">
            <div className="grid grid-cols-2 content-center gap-x-6 gap-y-7">
              <Metric label="掃描標的" value={data.total} />
              <Metric
                label="買進候選"
                value={data.buy_candidates}
                accent="text-gold-bright"
              />
              <Metric label="觀察候選" value={data.watch_candidates} accent="text-gold" />
              <Metric
                label="平均籌碼分數"
                value={data.avg_chip_score.toFixed(1)}
                accent={scoreColor(data.avg_chip_score)}
              />
            </div>

            <div className="hidden bg-line-soft lg:block" />

            <SignalDistribution counts={data.action_counts} total={data.total} />
          </div>
        </Card>
      </section>

      {/* 全市場熱力圖：方塊面積＝成交值，顏色可切漲跌／分數／法人 */}
      <section className="reveal" style={{ animationDelay: "120ms" }}>
        <MarketTreemap />
      </section>

      {/* 產業輪動矩陣 */}
      <section className="reveal" style={{ animationDelay: "200ms" }}>
        <IndustryHeatmap />
      </section>

      {/* 籌碼強度 Top 10 */}
      <div className="grid gap-6">
        <section
          className="reveal min-w-0"
          style={{ animationDelay: "240ms" }}
        >
          <Card className="p-0">
            <div className="px-4 pt-6 sm:px-6">
              <SectionTitle>籌碼強度 Top 10</SectionTitle>
            </div>
            <ul>
              {data.top.map((t, i) => (
                <li key={t.symbol}>
                  <Link
                    href={`/stocks/${t.symbol}`}
                    className="flex items-center gap-3 border-t border-line-soft px-4 py-3 transition-colors hover:bg-white/[0.02] sm:gap-4 sm:px-6"
                  >
                    <span className="w-4 shrink-0 font-mono text-xs text-ink-faint">
                      {i + 1}
                    </span>
                    <span className="shrink-0 font-mono text-sm font-medium text-ink">
                      {t.symbol}
                    </span>
                    <span className="min-w-0 truncate text-sm text-ink-dim">{t.name}</span>
                    <Change pct={t.change_pct} className="ml-auto text-xs" />
                    <span
                      className={`w-12 text-right font-mono text-sm font-bold tnum ${scoreColor(t.chip_score)}`}
                    >
                      {t.chip_score.toFixed(1)}
                    </span>
                    <ActionBadge action={t.action} shortOnMobile />
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        </section>
      </div>

      {/* 今日主力背離快報 */}
      {(bullish.length > 0 || bearish.length > 0) && (
        <section className="reveal" style={{ animationDelay: "320ms" }}>
          <div className="mb-4 flex items-end justify-between">
            <SectionTitle>今日主力背離</SectionTitle>
            <Link
              href="/divergence"
              className="font-mono text-xs text-gold transition-colors hover:text-gold-bright"
            >
              看全部 →
            </Link>
          </div>
          <div className="grid gap-6 md:grid-cols-2">
            <FlashList
              title="正背離"
              sub="價跌 · 主力買 → 疑逢低吸籌"
              tone="text-up"
              rows={bullish}
            />
            <FlashList
              title="負背離"
              sub="價漲 · 主力賣 → 疑逢高出貨"
              tone="text-down"
              rows={bearish}
            />
          </div>
          <p className="mt-3 text-[11px] text-ink-faint">
            60 日量價背離；回測方向正確但非單調、樣本半年，作為多訊號交叉驗證之一，非單獨交易依據。
          </p>
        </section>
      )}

      <p className="text-[11px] leading-relaxed text-ink-faint">
        盤中(intraday)分項僅在當日有盤後逐筆資料的標的計入加權；產業趨勢已接入但權重為 0（未經 OOS 驗證）。
        法人／集保／盤中皆無資料的標的不評分、不列入統計。
        價格漲跌以台股慣例顯示（<span className="text-up">紅漲</span> /{" "}
        <span className="text-down">綠跌</span>）。
      </p>
    </div>
  );
}

function FlashList({
  title,
  sub,
  tone,
  rows,
}: {
  title: string;
  sub: string;
  tone: string;
  rows: DivergenceScanRow[];
}) {
  return (
    <Card className="p-0">
      <div className="border-b border-line-soft px-4 py-4 sm:px-5">
        <div className={`font-mono text-sm font-bold ${tone}`}>{title}</div>
        <div className="mt-0.5 text-[11px] text-ink-faint">{sub}</div>
      </div>
      {rows.length === 0 ? (
        <div className="px-4 py-6 text-center text-xs text-ink-faint sm:px-5">
          今日無{title}標的
        </div>
      ) : (
        <ul>
          {rows.map((r) => (
            <li key={r.symbol}>
              <Link
                href={`/stocks/${r.symbol}?tab=flows`}
                className="flex items-center gap-3 border-t border-line-soft px-4 py-2.5 transition-colors hover:bg-white/[0.02] sm:px-5"
              >
                <span className="shrink-0 font-mono text-sm font-medium text-ink">
                  {r.symbol}
                </span>
                <span className="min-w-0 truncate text-xs text-ink-dim">{r.name}</span>
                <Change pct={r.change_pct} className="ml-auto text-xs" />
                <span
                  className={`w-16 text-right font-mono text-xs font-bold tnum ${
                    (r.flow_ratio ?? 0) > 0 ? "text-up" : "text-down"
                  }`}
                >
                  {r.flow_ratio != null
                    ? `${r.flow_ratio > 0 ? "+" : ""}${(r.flow_ratio * 100).toFixed(1)}%`
                    : "—"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** 決策強度由強到弱，橫條長度＝佔掃描標的比例。順序固定，不隨當日家數跳動。 */
const ACTION_ORDER: Action[] = ["BUY", "WATCH", "HOLD", "REDUCE", "EXIT", "AVOID"];

/** 橫條也分級：金色越亮＝越積極，減碼/出場/避開退成灰（動作一律金色系，不碰紅綠）。 */
const BAR_COLOR: Record<string, string> = {
  BUY: "bg-gold-bright",
  WATCH: "bg-gold",
  HOLD: "bg-gold-dim",
};

function SignalDistribution({
  counts,
  total,
}: {
  counts: Record<string, number>;
  total: number;
}) {
  const known = ACTION_ORDER.filter((a) => counts[a] != null);
  const rest = Object.keys(counts)
    .filter((a) => !ACTION_ORDER.includes(a as Action))
    .sort((a, b) => counts[b] - counts[a]);
  const rows = [...known, ...rest];

  return (
    <div className="min-w-0">
      <Label>訊號分布</Label>
      <div className="mt-4 space-y-3">
        {rows.map((action) => {
          const count = counts[action] ?? 0;
          const pct = total > 0 ? (count / total) * 100 : 0;
          return (
            <div key={action} className="flex items-center gap-3">
              <div className="w-14 shrink-0">
                <ActionBadge action={action as Action} />
              </div>
              <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-line-soft">
                {/* BUY 常只佔 2~3%，給個下限才看得出「有，但很少」 */}
                <div
                  className={`bar-fill h-full rounded-full ${BAR_COLOR[action] ?? "bg-ink-faint/70"}`}
                  style={{ width: `max(3px, ${pct}%)` }}
                />
              </div>
              <span className="w-9 shrink-0 text-right font-mono text-sm tnum text-ink-dim">
                {count}
              </span>
              <span className="w-11 shrink-0 text-right font-mono text-[11px] tnum text-ink-faint">
                {pct.toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Hero 報價欄：標籤 / 大字數值 / 漲跌。leading-none 讓相鄰報價的數字對齊同一基線。 */
function Quote({
  label,
  value,
  pts,
  pct,
}: {
  label: string;
  value: number | null | undefined;
  pts: number | null | undefined;
  pct: number | null | undefined;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <div className="mt-3 font-mono text-3xl font-bold tnum leading-none">
        {value?.toLocaleString("zh-TW") ?? "—"}
      </div>
      <PointChange pts={pts} pct={pct} />
    </div>
  );
}

/** 掃描統計欄（同一張卡內以 1px 線分欄，非獨立卡片）。 */
function Metric({
  label,
  value,
  accent = "text-ink",
}: {
  label: string;
  value: React.ReactNode;
  accent?: string;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <div className={`mt-3 font-mono text-3xl font-bold tnum leading-none ${accent}`}>
        {value}
      </div>
    </div>
  );
}

/** 指數/期貨的漲跌：點數 + 百分比（台股語意紅漲綠跌，色由 Change 提供）。 */
function PointChange({
  pts,
  pct,
}: {
  pts: number | null | undefined;
  pct: number | null | undefined;
}) {
  return (
    <div className="mt-2.5 flex items-baseline gap-2 text-sm">
      <span className={`font-mono tnum ${dirColor(pts)}`}>
        {pts == null
          ? "—"
          : `${pts > 0 ? "+" : ""}${pts.toLocaleString("zh-TW", {
              maximumFractionDigits: 2,
            })}`}
      </span>
      <Change pct={pct} className="text-sm" />
    </div>
  );
}

/** 脈絡欄的補充行：對齊左側報價的漲跌行，右區才不會只有兩行、底部空一截。 */
function SubNote({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-2.5 flex items-center gap-1 font-mono text-xs tnum text-ink-faint">
      {children}
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
