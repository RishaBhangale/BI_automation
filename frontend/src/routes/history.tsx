import { createFileRoute } from "@tanstack/react-router";
import { useState, useRef, useEffect, useMemo } from "react";
import {
  Download, Eye, FileText, Globe, ChevronDown,
  ArrowUpDown, ArrowUp, ArrowDown,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ReportModal } from "@/components/app/report-modal";
import { fetchRuns, exportRun, type Run } from "@/lib/api-client";
import { useQuery } from "@tanstack/react-query";

export const Route = createFileRoute("/history")({
  head: () => ({
    meta: [
      { title: "Test History — Automated BI Testing - Validation" },
      {
        name: "description",
        content: "Browse past validation runs, drill into scenario results and export reports.",
      },
    ],
  }),
  component: History,
});

// ── Export dropdown ───────────────────────────────────────────────────────────

function ExportButton({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const doExport = async (format: "pdf" | "html") => {
    setOpen(false);
    setBusy(true);
    toast.info(`Rendering ${format.toUpperCase()} for ${runId}…`);
    try {
      const url = await exportRun(runId, format);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${runId}.${format}`;
      a.click();
      toast.success(`Export complete · ${runId}.${format}`);
    } catch (e: unknown) {
      toast.error("Export failed: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={ref} className="relative">
      <Button variant="ghost" size="sm" disabled={busy} onClick={() => setOpen((o) => !o)}>
        <Download className="size-4" />
        {busy ? "Exporting…" : "Export"}
        <ChevronDown className="ml-1 size-3.5" />
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-40 rounded-lg border border-border bg-card shadow-lg">
          <button
            className="flex w-full items-center gap-2 rounded-t-lg px-3 py-2 text-sm hover:bg-muted/60"
            onClick={() => doExport("pdf")}
          >
            <FileText className="size-4 text-muted-foreground" /> Export as PDF
          </button>
          <button
            className="flex w-full items-center gap-2 rounded-b-lg px-3 py-2 text-sm hover:bg-muted/60"
            onClick={() => doExport("html")}
          >
            <Globe className="size-4 text-muted-foreground" /> Export as HTML
          </button>
        </div>
      )}
    </div>
  );
}

// ── Sort helpers ──────────────────────────────────────────────────────────────

type SortKey = "runId" | "startedAt" | "total" | "passed" | "failed" | "passRate" | "duration";
type SortDir = "asc" | "desc";

function passRate(run: Run) {
  return run.total > 0 ? Math.round((run.passed / run.total) * 100) : 0;
}

function durationSecs(dur: string | null | undefined): number {
  if (!dur) return 0;
  const m = dur.match(/(\d+)m\s+(\d+)s/);
  return m ? parseInt(m[1]!) * 60 + parseInt(m[2]!) : 0;
}

function SortIcon({ col, sortKey, sortDir }: { col: SortKey; sortKey: SortKey | null; sortDir: SortDir }) {
  if (col !== sortKey) return <ArrowUpDown className="ml-1 inline size-3 opacity-40" />;
  return sortDir === "asc"
    ? <ArrowUp className="ml-1 inline size-3 text-primary" />
    : <ArrowDown className="ml-1 inline size-3 text-primary" />;
}

// ── History page ──────────────────────────────────────────────────────────────

function History() {
  const { data, isLoading: loading } = useQuery({ queryKey: ["runs"], queryFn: fetchRuns });
  const runs = data ?? [];
  const [active, setActive] = useState<Run | null>(null);

  // Sort state
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      // Cycle: asc → desc → off
      if (sortDir === "asc") setSortDir("desc");
      else { setSortKey(null); setSortDir("asc"); }
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  const sortedRuns = useMemo(() => {
    if (!sortKey) return runs;
    return [...runs].sort((a, b) => {
      let av: number | string = 0;
      let bv: number | string = 0;
      if (sortKey === "runId")     { av = a.runId;      bv = b.runId; }
      if (sortKey === "startedAt") { av = new Date(a.startedAt).getTime(); bv = new Date(b.startedAt).getTime(); }
      if (sortKey === "total")     { av = a.total;      bv = b.total; }
      if (sortKey === "passed")    { av = a.passed;     bv = b.passed; }
      if (sortKey === "failed")    { av = a.failed;     bv = b.failed; }
      if (sortKey === "passRate")  { av = passRate(a);  bv = passRate(b); }
      if (sortKey === "duration")  { av = durationSecs(a.duration); bv = durationSecs(b.duration); }
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
  }, [runs, sortKey, sortDir]);

  // Helper to build a sortable <TableHead>
  function SortHead({ col, label, className }: { col: SortKey; label: string; className?: string }) {
    return (
      <TableHead className={className}>
        <button
          className={"flex items-center gap-0.5 cursor-pointer select-none hover:text-foreground transition-colors " + (sortKey === col ? "text-primary font-semibold" : "")}
          onClick={() => handleSort(col)}
        >
          {label}
          <SortIcon col={col} sortKey={sortKey} sortDir={sortDir} />
        </button>
      </TableHead>
    );
  }

  return (
    <div className="space-y-6 p-6 lg:p-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Test History</h1>
        <p className="mt-1 text-sm text-muted-foreground">All archived validation runs.</p>
      </div>

      <Card className="border-border/70 p-0">
        {loading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 rounded-md" />
            ))}
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <SortHead col="runId"     label="Run ID" />
                <SortHead col="startedAt" label="Date / Time" />
                <TableHead>Config Used</TableHead>
                <SortHead col="total"     label="Total"    className="text-right" />
                <SortHead col="passed"    label="Passed"   className="text-right" />
                <SortHead col="failed"    label="Failed"   className="text-right" />
                <SortHead col="passRate"  label="Pass %"   className="text-right" />
                <SortHead col="duration"  label="Duration" />
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedRuns.map((run) => {
                const rate = passRate(run);
                const rateColour =
                  rate >= 80 ? "text-success" :
                  rate >= 50 ? "text-amber-500" :
                  "text-destructive";
                return (
                  <TableRow key={run.runId}>
                    <TableCell className="font-mono text-xs text-primary">{run.runId}</TableCell>
                    <TableCell className="text-sm">{new Date(run.startedAt).toLocaleString()}</TableCell>
                    <TableCell className="font-mono text-xs">{run.config}</TableCell>
                    <TableCell className="text-right tabular-nums">{run.total}</TableCell>
                    <TableCell className="text-right tabular-nums text-success">{run.passed}</TableCell>
                    <TableCell className="text-right tabular-nums text-destructive">{run.failed}</TableCell>
                    <TableCell className={`text-right tabular-nums font-medium ${rateColour}`}>
                      {run.total > 0 ? `${rate}%` : "—"}
                    </TableCell>
                    <TableCell className="text-sm">{run.duration}</TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="sm" onClick={() => setActive(run)}>
                          <Eye className="size-4" /> View Report
                        </Button>
                        <ExportButton runId={run.runId} />
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Card>

      <ReportModal run={active} onClose={() => setActive(null)} />
    </div>
  );
}
