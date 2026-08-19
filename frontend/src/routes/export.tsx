import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Download, FileText, Globe } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { MetricCard, StatusBadge } from "@/components/app/metric-card";
import { fetchRuns, exportRun } from "@/lib/api-client";
import { useQuery } from "@tanstack/react-query";

export const Route = createFileRoute("/export")({
  head: () => ({
    meta: [
      { title: "Export — Automated BI Testing - Validation" },
      {
        name: "description",
        content: "Export any archived validation run as a shareable PDF or HTML report.",
      },
      { property: "og:title", content: "Export — Automated BI Testing - Validation" },
      { property: "og:description", content: "Export validation runs as PDF or HTML reports." },
    ],
  }),
  component: ExportPage,
});

function ExportPage() {
  const { data: runsData, isLoading: loading } = useQuery({ queryKey: ['runs'], queryFn: fetchRuns });
  const runs = runsData ?? [];
  const [runId, setRunId] = useState("");
  const [format, setFormat] = useState<"PDF" | "HTML">("PDF");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (runs.length > 0 && !runId) {
      setRunId(runs[0]!.runId);
    }
  }, [runs, runId]);

  const run = runs.find((r) => r.runId === runId);

  const doExport = async () => {
    if (!run) return;
    setBusy(true);
    toast.info(`Rendering ${format} report for ${run.runId}…`);
    try {
      const url = await exportRun(run.runId, format.toLowerCase() as "pdf" | "html");
      const a = document.createElement("a");
      a.href = url;
      a.download = `${run.runId}.${format.toLowerCase()}`;
      a.click();
      toast.success(`Export complete · ${run.runId}.${format.toLowerCase()}`);
    } catch (e: unknown) {
      toast.error("Export failed: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6 p-6 lg:p-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Export</h1>
        <p className="mt-1 text-sm text-muted-foreground">Generate a shareable report for any past run.</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_1.4fr]">
        <Card className="h-fit border-border/70 p-5">
          <div className="space-y-2">
            <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Past run
            </label>
            <Select value={runId} onValueChange={setRunId}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {runs.map((r) => (
                  <SelectItem key={r.runId} value={r.runId}>
                    {r.runId} — {new Date(r.startedAt).toLocaleString()}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2 mt-4">
            <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Format
            </label>
            <div className="grid grid-cols-2 gap-3">
              {(["PDF", "HTML"] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setFormat(f)}
                  className={
                    "flex items-center gap-2 rounded-lg border p-3 text-sm transition-colors " +
                    (format === f
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border/70 text-muted-foreground hover:border-primary/50")
                  }
                >
                  {f === "PDF" ? <FileText className="size-4" /> : <Globe className="size-4" />}
                  {f}
                </button>
              ))}
            </div>
          </div>

          <Button className="w-full mt-6" size="lg" onClick={doExport} disabled={busy || !run}>
            <Download className="size-4" /> {busy ? "Exporting…" : "Export & Download"}
          </Button>
        </Card>

        <Card className="border-border/70 bg-panel p-5">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">Report preview</h2>
            <StatusBadge failed={run?.failed || 0} />
          </div>
          {loading || !run ? (
            <Skeleton className="h-72 w-full rounded-lg mt-4" />
          ) : (
            <div className="rounded-lg border border-border/70 bg-background/60 p-5 mt-4">
              <p className="text-xs uppercase tracking-widest text-muted-foreground">
                BI Validator report
              </p>
              <p className="mt-1 text-lg font-semibold">{run.runId}</p>
              <p className="font-mono text-xs text-muted-foreground">
                {run.config} · {new Date(run.startedAt).toLocaleString()}
              </p>
              <div className="mt-4 grid gap-3 sm:grid-cols-4">
                <MetricCard label="Total" value={run.total} />
                <MetricCard label="Passed" value={run.passed} tone="success" />
                <MetricCard label="Failed" value={run.failed} tone="danger" />
                <MetricCard label="Duration" value={run.duration || "-"} />
              </div>
              <div className="mt-4 space-y-1">
                {run.results.slice(0, 5).map((r) => {
                  const isPass = (r.status || "").toLowerCase() === "passed" || (r.status || "").toLowerCase() === "pass";
                  return (
                  <div
                    key={r.tc_id}
                    className="flex items-center justify-between rounded-md bg-muted/40 px-3 py-2 text-xs"
                  >
                    <span className="truncate">
                      <span className="font-mono text-primary">{r.tc_id}</span> {r.name}
                    </span>
                    <span className={isPass ? "text-success" : "text-destructive"}>
                      {(r.status || "").toUpperCase()}
                    </span>
                  </div>
                )})}
                <p className="pt-1 text-xs text-muted-foreground">
                  + {Math.max(run.results.length - 5, 0)} more scenarios in full report
                </p>
              </div>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}