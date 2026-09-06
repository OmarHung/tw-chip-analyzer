import Link from "next/link";
import { ActionBadge } from "@/components/ActionBadge";
import { Card } from "@/components/Card";
import { ScoreBar } from "@/components/ScoreBar";
import { api } from "@/lib/api";
import { fmtPrice, scoreColor } from "@/lib/format";

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
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-8 text-center text-slate-400">
        找不到 {symbol} 的分析資料。
        <div className="mt-4">
          <Link href="/scanner" className="text-emerald-400 hover:underline">
            ← 回 Scanner
          </Link>
        </div>
      </div>
    );
  }

  const { scores, risk } = data;

  return (
    <div className="space-y-6">
      <Link href="/scanner" className="text-sm text-slate-400 hover:text-white">
        ← 回 Scanner
      </Link>

      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-3xl font-bold">{data.symbol}</h1>
        <span className="text-2xl tabular-nums text-slate-300">
          {fmtPrice(data.price)}
        </span>
        <ActionBadge action={data.action} />
        <div className="ml-auto text-right">
          <div className="text-xs text-slate-500">Chip Score</div>
          <div className={`text-4xl font-bold tabular-nums ${scoreColor(data.chip_score)}`}>
            {data.chip_score.toFixed(1)}
          </div>
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <h2 className="mb-4 text-sm font-semibold text-slate-300">分數拆解</h2>
          <div className="space-y-4">
            <ScoreBar label="盤中 Intraday" score={scores.intraday} />
            <ScoreBar label="法人 Institutional" score={scores.institutional} />
            <ScoreBar label="TDCC Holder" score={scores.holder} />
            <ScoreBar label="市場 Market" score={scores.market} />
          </div>
          <p className="mt-4 text-xs text-slate-600">
            盤中分數於 Phase 1 為中性（無即時資料，已排除於加權）。
          </p>
        </Card>

        <Card>
          <h2 className="mb-4 text-sm font-semibold text-slate-300">
            風險/報酬計畫
          </h2>
          <dl className="grid grid-cols-2 gap-y-3 text-sm">
            <Row label="進場區間">
              {data.entry
                ? `${fmtPrice(data.entry.low)} – ${fmtPrice(data.entry.high)}`
                : "—"}
            </Row>
            <Row label="停損">
              {risk.stop_loss != null ? fmtPrice(risk.stop_loss) : "—"}
            </Row>
            <Row label="TP1">
              {risk.tp1 != null ? fmtPrice(risk.tp1) : "—"}
            </Row>
            <Row label="TP2">
              {risk.tp2 != null ? fmtPrice(risk.tp2) : "—"}
            </Row>
            <Row label="風險報酬 RR">
              <span className="font-semibold text-emerald-400">
                {risk.rr != null ? risk.rr.toFixed(2) : "—"}
              </span>
            </Row>
          </dl>
        </Card>
      </div>

      <Card>
        <h2 className="mb-3 text-sm font-semibold text-slate-300">訊號原因</h2>
        {data.reasons.length ? (
          <ul className="space-y-1.5 text-sm text-slate-300">
            {data.reasons.map((r, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-emerald-500">›</span>
                {r}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500">無顯著訊號</p>
        )}
      </Card>

      <Card className="border-dashed">
        <h2 className="mb-2 text-sm font-semibold text-slate-300">
          價格 / CVD / 法人 / TDCC 走勢圖
        </h2>
        <p className="text-sm text-slate-500">
          K 線、CVD、大單淨量、OBI、Absorption、法人買賣超、融資、TDCC 持股比等時間序列圖，
          需後端提供時序資料 API（Phase 3 realtime 與盤後 importer 完成後補上）。
        </p>
      </Card>
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <dt className="text-slate-400">{label}</dt>
      <dd className="text-right tabular-nums">{children}</dd>
    </>
  );
}
