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
import { Activity, CheckCircle2, Timer, XCircle } from "lucide-react";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { MetricCard, StatusBadge } from "@/components/app/metric-card";
import { ReportModal } from "@/components/app/report-modal";
import { fetchRuns, fetchConfigs, type Run } from "@/lib/api-client";
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

function Dashboard() {
  const { data: runsData, isLoading: runsLoading } = useQuery({ queryKey: ["runs"], queryFn: fetchRuns });
  const { data: configsData, isLoading: configsLoading } = useQuery({ queryKey: ["configs"], queryFn: fetchConfigs });
  const loading = runsLoading || configsLoading;
  const [active, setActive] = useState<Run | null>(null);

  const runs = runsData ?? [];
  const totalPassed = runs.reduce((acc, r) => acc + (r.passed || 0), 0);
  const totalFailed = runs.reduce((acc, r) => acc + (r.failed || 0), 0);
  const last7 = runs.slice(0, 7);

  const totalSecs = last7.reduce((acc, r) => {
    const m = (r.duration || "").match(/(\d+)m\s+(\d+)s/);
    return acc + (m ? parseInt(m[1] as string) * 60 + parseInt(m[2] as string) : 0);
  }, 0);
  const avgSecs = last7.length ? totalSecs / last7.length : 0;
  const avgDuration = `${Math.floor(avgSecs / 60)}m ${Math.floor(avgSecs % 60).toString().padStart(2, "0")}s`;

  const stats = {
    totalRuns: runs.length,
    passed: totalPassed,
    failed: totalFailed,
    avgDuration,
  };

  const chartData = [...runs].reverse().slice(-7).map((r) => ({
    run: r.runId.split("-")[1] ?? r.runId,
    passed: r.passed,
    failed: r.failed,
  }));

  return (
    <div className="space-y-8 p-6 lg:p-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Validation health across all configured BI dashboards.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {loading
          ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[124px] rounded-xl" />)
          : [
              { label: "Total Runs", value: stats.totalRuns, hint: "since Jan 2026", icon: Activity },
              {
                label: "Passed",
                value: stats.passed,
                hint: "assertions in last 7 runs",
                icon: CheckCircle2,
                tone: "success" as const,
              },
              {
                label: "Failed",
                value: stats.failed,
                hint: "assertions in last 7 runs",
                icon: XCircle,
                tone: "danger" as const,
              },
              { label: "Avg Duration", value: stats.avgDuration, hint: "per full suite", icon: Timer },
            ].map((m) => <MetricCard key={m.label} {...m} />)}
      </div>

      <Card className="border-border/70 p-6">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold">Pass / fail by run</h2>
            <p className="text-xs text-muted-foreground">Last 7 executions</p>
          </div>
        </div>
        {loading ? (
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
          {loading
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
                        <p className="font-medium">{run.runId}</p>
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
