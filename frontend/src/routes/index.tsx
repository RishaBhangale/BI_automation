import { createFileRoute } from "@tanstack/react-router";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CheckCircle2, Timer, TrendingDown, TrendingUp, XCircle } from "lucide-react";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { MetricCard, StatusBadge } from "@/components/app/metric-card";
import { ReportModal } from "@/components/app/report-modal";
import { fetchRuns, type Run } from "@/lib/api-client";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Dashboard — Automated BI Testing - Validation" },
      {
        name: "description",
        content: "Live overview of BI dashboard validation runs, pass/fail trends and durations.",
      },
      { property: "og:title", content: "Dashboard — Automated BI Testing - Validation" },
      { property: "og:description", content: "Overview of BI validation runs and pass/fail trends." },
    ],
  }),
  component: Dashboard,
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function parseDurationSecs(dur: string | null | undefined): number {
  if (!dur) return 0;
  const m = dur.match(/(\d+)m\s+(\d+)s/);
  return m ? parseInt(m[1] as string) * 60 + parseInt(m[2] as string) : 0;
}

function formatSecs(secs: number): string {
  return `${Math.floor(secs / 60)}m ${Math.floor(secs % 60).toString().padStart(2, "0")}s`;
}

function passRate(run: Run): number {
  if (!run.total) return 0;
  return Math.round((run.passed / run.total) * 100);
}

// ── Dashboard Component ────────────────────────────────────────────────────────

function Dashboard() {
  const { data: runsData, isLoading } = useQuery({ queryKey: ["runs"], queryFn: fetchRuns });
  const [active, setActive] = useState<Run | null>(null);

  const runs = (runsData ?? []).filter((r) => r.status === "finished");

  // ── Metrics ────────────────────────────────────────────────────────────────
  const lastRun = runs[0] ?? null;
  const lastRunPassRate = lastRun ? passRate(lastRun) : null;

  // 7-day trend: compare avg pass rate of last 7 vs prior 7
  const last7 = runs.slice(0, 7);
  const prior7 = runs.slice(7, 14);
  const avgRate = (arr: Run[]) =>
    arr.length ? arr.reduce((s, r) => s + passRate(r), 0) / arr.length : null;
  const last7Avg = avgRate(last7);
  const prior7Avg = avgRate(prior7);
  const trendDelta =
    last7Avg !== null && prior7Avg !== null ? Math.round(last7Avg - prior7Avg) : null;

  // Avg duration over last 7
  const totalSecs = last7.reduce((acc, r) => acc + parseDurationSecs(r.duration), 0);
  const avgDuration = last7.length ? formatSecs(totalSecs / last7.length) : "—";

  // Chart data (last 7, oldest first)
  const chartData = [...last7].reverse().map((r) => ({
    run: r.runId.split("-")[0] ?? r.runId,
    passed: r.passed,
    failed: r.failed,
  }));

  // ── KPI tiles ──────────────────────────────────────────────────────────────
  const kpis = [
    {
      label: "Last Run Status",
      value: lastRun ? (lastRun.failed === 0 ? "PASS" : "FAIL") : "—",
      hint: lastRun ? new Date(lastRun.startedAt).toLocaleString() : "No runs yet",
      icon: lastRun?.failed === 0 ? CheckCircle2 : XCircle,
      tone: (lastRun?.failed === 0 ? "success" : "danger") as "success" | "danger" | undefined,
    },
    {
      label: "Last Run Pass Rate",
      value: lastRunPassRate !== null ? `${lastRunPassRate}%` : "—",
      hint: lastRun ? `${lastRun.passed} passed / ${lastRun.total} total` : "—",
      icon: CheckCircle2,
      tone: lastRunPassRate !== null && lastRunPassRate >= 80 ? ("success" as const) : ("danger" as const),
    },
    {
      label: "7-Day Trend",
      value:
        trendDelta !== null
          ? `${trendDelta >= 0 ? "+" : ""}${trendDelta}%`
          : "—",
      hint: trendDelta !== null
        ? `vs prior 7 runs · avg ${last7Avg !== null ? Math.round(last7Avg) : "—"}%`
        : "Not enough runs",
      icon: trendDelta !== null && trendDelta >= 0 ? TrendingUp : TrendingDown,
      tone: trendDelta !== null && trendDelta >= 0 ? ("success" as const) : ("danger" as const),
    },
    {
      label: "Avg Duration",
      value: avgDuration,
      hint: "per full suite (last 7 runs)",
      icon: Timer,
      tone: undefined,
    },
  ];

  return (
    <div className="space-y-8 p-6 lg:p-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Validation health across all configured BI dashboards.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {isLoading
          ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[124px] rounded-xl" />)
          : kpis.map((m) => <MetricCard key={m.label} {...m} />)}
      </div>

      <Card className="border-border/70 p-6">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold">Pass / fail by run</h2>
            <p className="text-xs text-muted-foreground">Last 7 completed executions</p>
          </div>
        </div>
        {isLoading ? (
          <Skeleton className="h-72 w-full rounded-lg" />
        ) : (
          <div className="h-72 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} barGap={6}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
                <XAxis dataKey="run" stroke="var(--color-muted-foreground)" fontSize={12} tickLine={false} />
                <YAxis stroke="var(--color-muted-foreground)" fontSize={12} tickLine={false} allowDecimals={false} />
                <Tooltip
                  contentStyle={{
                    background: "var(--color-card)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 8,
                    color: "var(--color-foreground)",
                    fontSize: 12,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="passed" name="Passed" fill="var(--color-success)" radius={[4, 4, 0, 0]} />
                <Bar dataKey="failed" name="Failed" fill="var(--color-destructive)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      <div>
        <h2 className="mb-4 text-sm font-semibold">Latest runs</h2>
        <div className="grid gap-4 lg:grid-cols-3">
          {isLoading
            ? Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-40 rounded-xl" />)
            : runs.slice(0, 3).map((run) => (
                <button
                  key={run.runId}
                  className="block w-full text-left"
                  onClick={() => setActive(run)}
                >
                  <Card className="border-border/70 gap-0 p-5 transition-colors hover:border-primary/50 hover:bg-muted/30 cursor-pointer">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="font-medium font-mono text-sm">{run.runId}</p>
                        <p className="text-xs text-muted-foreground">{new Date(run.startedAt).toLocaleString()}</p>
                      </div>
                      <StatusBadge failed={run.failed} />
                    </div>
                    <div className="mt-5 grid grid-cols-3 gap-2 text-center">
                      <div className="rounded-md bg-muted/50 py-2">
                        <p className="text-lg font-semibold tabular-nums">{run.total}</p>
                        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Total</p>
                      </div>
                      <div className="rounded-md bg-muted/50 py-2">
                        <p className="text-lg font-semibold tabular-nums text-success">{run.passed}</p>
                        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Passed</p>
                      </div>
                      <div className="rounded-md bg-muted/50 py-2">
                        <p className="text-lg font-semibold tabular-nums text-destructive">{run.failed}</p>
                        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Failed</p>
                      </div>
                    </div>
                    <div className="mt-4 flex items-center justify-between text-xs text-muted-foreground">
                      <span className="font-mono">{run.config}</span>
                      <span className="flex items-center gap-1">
                        <Timer className="size-3" /> {run.duration}
                      </span>
                    </div>
                  </Card>
                </button>
              ))}
        </div>
      </div>

      <ReportModal run={active} onClose={() => setActive(null)} />
    </div>
  );
}
