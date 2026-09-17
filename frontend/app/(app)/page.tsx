import Link from "next/link";
import { ActionBadge } from "@/components/ActionBadge";
import { Card, SectionTitle, StatCard } from "@/components/Card";
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
            <div className="grid grid-cols-2 gap-6 md:grid-cols-3 xl:grid-cols-5">
              <div>
                <Label>TAIEX 加權指數</Label>
                <div className="mt-2 font-mono text-3xl font-bold tnum">
                  {m.taiex_close?.toLocaleString("zh-TW") ?? "—"}
                </div>
                <PointChange pts={m.taiex_change} pct={m.taiex_change_pct} />
              </div>
              <div>
                <Label>
                  台指期{m.futures ? ` ${m.futures.contract_month}` : ""}
                </Label>
                <div className="mt-2 font-mono text-3xl font-bold tnum">
                  {m.futures?.close?.toLocaleString("zh-TW") ?? "—"}
                </div>
                <PointChange
                  pts={m.futures?.change ?? null}
                  pct={m.futures?.change_pct ?? null}
                />
              </div>
              <div>
                <Label>大盤氣氛</Label>
                <div className="mt-2 flex items-center gap-2">
                  <span
                    className={`font-display text-3xl ${dirColor(m.trend_score)}`}
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

      {/* 統計卡 + 訊號分布（lg 以下訊號分布自己佔一行，橫條才不會擠成一團） */}
      <section
        className="reveal grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-7"
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
        <div className="col-span-2 min-w-0 md:col-span-4 lg:col-span-3">
          <SignalDistribution counts={data.action_counts} total={data.total} />
        </div>
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
                    <ActionBadge action={t.action} />
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

/** 各 action 家數的橫條分布（長度＝佔掃描標的比例）。 */
function SignalDistribution({
  counts,
  total,
}: {
  counts: Record<string, number>;
  total: number;
}) {
  return (
    <Card className="flex h-full flex-col justify-center">
      <SectionTitle>訊號分布</SectionTitle>
      <div className="space-y-3">
        {Object.entries(counts)
          .sort((a, b) => b[1] - a[1])
          .map(([action, count]) => (
            <div key={action} className="flex items-center gap-3">
              <div className="w-14">
                <ActionBadge action={action as Action} />
              </div>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-line-soft">
                <div
                  className="bar-fill h-full rounded-full bg-gold-dim"
                  style={{ width: `${(count / total) * 100}%` }}
                />
              </div>
              <span className="w-10 text-right font-mono text-sm tnum text-ink-dim">
                {count}
              </span>
            </div>
          ))}
      </div>
    </Card>
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
    <div className="mt-1 flex items-baseline gap-2 text-sm">
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
