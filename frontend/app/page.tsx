import Link from "next/link";
import { ActionBadge } from "@/components/ActionBadge";
import { Card, StatCard } from "@/components/Card";
import { api, type Action } from "@/lib/api";
import { scoreColor } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  let data;
  try {
    data = await api.dashboard();
  } catch {
    return (
      <ErrorState message="無法連線後端 API，請確認 uvicorn 執行於 :8099。" />
    );
  }

  if (data.total === 0) {
    return (
      <ErrorState message="尚無特徵資料。請先執行 scripts.seed_dev 匯入示範資料。" />
    );
  }

  return (
    <div className="space-y-8">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold">市場概覽</h1>
          <p className="mt-1 text-sm text-slate-400">資料日期 {data.as_of}</p>
        </div>
        <Link
          href="/scanner"
          className="rounded-lg bg-emerald-500/90 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400"
        >
          前往 Scanner →
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="掃描標的" value={data.total} />
        <StatCard
          label="BUY 候選"
          value={data.buy_candidates}
          accent="text-emerald-400"
        />
        <StatCard
          label="WATCH 候選"
          value={data.watch_candidates}
          accent="text-amber-400"
        />
        <StatCard
          label="平均 Chip Score"
          value={data.avg_chip_score.toFixed(1)}
          accent={scoreColor(data.avg_chip_score)}
        />
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <h2 className="mb-4 text-sm font-semibold text-slate-300">訊號分布</h2>
          <div className="space-y-2">
            {Object.entries(data.action_counts)
              .sort((a, b) => b[1] - a[1])
              .map(([action, count]) => {
                const pct = (count / data.total) * 100;
                return (
                  <div key={action} className="flex items-center gap-3">
                    <div className="w-16">
                      <ActionBadge action={action as Action} />
                    </div>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-800">
                      <div
                        className="h-full rounded-full bg-slate-500"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="w-8 text-right text-sm tabular-nums text-slate-400">
                      {count}
                    </span>
                  </div>
                );
              })}
          </div>
        </Card>

        <Card>
          <h2 className="mb-4 text-sm font-semibold text-slate-300">
            Top 籌碼強度
          </h2>
          <ul className="divide-y divide-slate-800">
            {data.top.map((t, i) => (
              <li key={t.symbol}>
                <Link
                  href={`/stocks/${t.symbol}`}
                  className="flex items-center gap-3 py-2 hover:opacity-80"
                >
                  <span className="w-5 text-xs text-slate-500">{i + 1}</span>
                  <span className="font-medium">{t.symbol}</span>
                  <span
                    className={`ml-auto font-semibold tabular-nums ${scoreColor(t.chip_score)}`}
                  >
                    {t.chip_score.toFixed(1)}
                  </span>
                  <ActionBadge action={t.action} />
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <p className="text-xs text-slate-600">
        註：TAIEX、大盤 regime、漲跌家數等指標需接入大盤資料源，將於後續版本補上。
      </p>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-8 text-center text-slate-400">
      {message}
    </div>
  );
}
