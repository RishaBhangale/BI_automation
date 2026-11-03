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
      { title: "Validation History — Automated BI Validation" },
      {
        name: "description",
        content: "Browse past validation runs, drill into scenario results and export reports.",
      },
    ],
  }),
  component: ValidationHistory,
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
    <div ref={ref} className="relative inline-block text-left">
      <Button
        variant="ghost"
        size="sm"
        disabled={busy}
        onClick={() => setOpen((o) => !o)}
        className="h-7 gap-1 px-2 text-xs font-medium text-foreground hover:bg-muted/60 whitespace-nowrap"
      >
        <Download className="size-3.5" />
        {busy ? "Downloading…" : "Download"}
        <ChevronDown className="size-3 text-muted-foreground ml-0.5" />
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 w-28 rounded-lg border border-border bg-card shadow-lg py-1">
          <button
            className="flex w-full items-center gap-2 px-3 py-1.5 text-xs font-medium hover:bg-muted/60 transition-colors whitespace-nowrap"
            onClick={() => doExport("pdf")}
          >
            <FileText className="size-3.5 text-muted-foreground" /> PDF
          </button>
          <button
            className="flex w-full items-center gap-2 px-3 py-1.5 text-xs font-medium hover:bg-muted/60 transition-colors whitespace-nowrap"
            onClick={() => doExport("html")}
          >
            <Globe className="size-3.5 text-muted-foreground" /> HTML
          </button>
        </div>
      )}
    </div>
  );
}

// ── Status badge pill ─────────────────────────────────────────────────────────

function StatusBadge({ run }: { run: Run }) {
  if (run.status === "running") {
    return (
      <span className="inline-flex items-center rounded-full bg-blue-500/10 px-2.5 py-0.5 text-[11px] font-semibold text-blue-600 dark:text-blue-400 border border-blue-500/20 whitespace-nowrap">
        Running
      </span>
    );
  }
  if (run.failed > 0) {
    return (
      <span className="inline-flex items-center rounded-full bg-rose-500/10 px-2.5 py-0.5 text-[11px] font-semibold text-rose-600 dark:text-rose-400 border border-rose-500/20 whitespace-nowrap">
        Failed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 whitespace-nowrap">
      Completed
    </span>
  );
}

// ── Format helpers ────────────────────────────────────────────────────────────

function formatDateTime(iso: string): string {
  try {
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, "0");
    const day = pad(d.getDate());
    const month = pad(d.getMonth() + 1);
    const year = d.getFullYear();
    const hours = pad(d.getHours());
    const mins = pad(d.getMinutes());
    const secs = pad(d.getSeconds());
    return `${day}/${month}/${year}, ${hours}:${mins}:${secs}`;
  } catch {
    return iso;
  }
}

type SortKey = "runId" | "startedAt" | "total" | "passed" | "failed" | "skipped" | "passRate" | "duration";
type SortDir = "asc" | "desc";

function passRate(run: Run) {
  return run.total > 0 ? Math.round((run.passed / run.total) * 100) : 0;
}

function durationSecs(dur: string | null | undefined): number {
  if (!dur) return 0;
  const m = dur.match(/(\d+)m\s+(\d+)s/);
  if (m) return parseInt(m[1]!) * 60 + parseInt(m[2]!);
  const sOnly = dur.match(/(\d+)s/);
  if (sOnly) return parseInt(sOnly[1]!);
  return 0;
}

function SortIcon({ col, sortKey, sortDir }: { col: SortKey; sortKey: SortKey | null; sortDir: SortDir }) {
  if (col !== sortKey) return <ArrowUpDown className="ml-1 inline size-3 opacity-30 shrink-0" />;
  return sortDir === "asc"
    ? <ArrowUp className="ml-1 inline size-3 text-primary shrink-0" />
    : <ArrowDown className="ml-1 inline size-3 text-primary shrink-0" />;
}

const PAGE_SIZE = 10;

// ── Validation History page ───────────────────────────────────────────────────

function ValidationHistory() {
  const { data, isLoading: loading } = useQuery({ queryKey: ["runs"], queryFn: fetchRuns });
  const runs = data ?? [];
  const [active, setActive] = useState<Run | null>(null);

  // Sort state
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  // Pagination state (10 per page)
  const [currentPage, setCurrentPage] = useState(1);

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      if (sortDir === "asc") setSortDir("desc");
      else { setSortKey(null); setSortDir("asc"); }
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
    setCurrentPage(1);
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
      if (sortKey === "skipped")   {
        av = a.skipped ?? (a.results?.filter((r) => r.status === "skipped").length ?? 0);
        bv = b.skipped ?? (b.results?.filter((r) => r.status === "skipped").length ?? 0);
      }
      if (sortKey === "passRate")  { av = passRate(a);  bv = passRate(b); }
      if (sortKey === "duration")  { av = durationSecs(a.duration); bv = durationSecs(b.duration); }
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
  }, [runs, sortKey, sortDir]);

  // Pagination calculations
  const totalItems = sortedRuns.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / PAGE_SIZE));
  const validCurrentPage = Math.min(currentPage, totalPages);
  const startIndex = (validCurrentPage - 1) * PAGE_SIZE;
  const endIndex = Math.min(startIndex + PAGE_SIZE, totalItems);
  const paginatedRuns = sortedRuns.slice(startIndex, endIndex);

  // Helper to build a sortable <TableHead> with strict single-line whitespace-nowrap
  function SortHead({
    col,
    label,
    align = "left",
    className = "",
  }: {
    col: SortKey;
    label: string;
    align?: "left" | "center" | "right";
    className?: string;
  }) {
    const isCurrent = sortKey === col;
    const alignClass =
      align === "center"
        ? "justify-center text-center"
        : align === "right"
        ? "justify-end text-right"
        : "justify-start text-left";

    return (
      <TableHead className={`whitespace-nowrap px-2 py-2.5 text-xs font-semibold ${align === "center" ? "text-center" : align === "right" ? "text-right" : "text-left"} ${className}`}>
        <button
          type="button"
          className={`inline-flex items-center gap-0.5 whitespace-nowrap cursor-pointer select-none transition-colors hover:text-foreground ${alignClass} ${
            isCurrent ? "text-primary font-bold" : "text-muted-foreground"
          }`}
          onClick={() => handleSort(col)}
        >
          <span className="whitespace-nowrap">{label}</span>
          <SortIcon col={col} sortKey={sortKey} sortDir={sortDir} />
        </button>
      </TableHead>
    );
  }

  return (
    <div className="space-y-6 p-6 lg:p-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Validation History</h1>
        <p className="mt-1 text-sm text-muted-foreground">All archived validation runs.</p>
      </div>

      <Card className="border-border/70 p-0 shadow-sm overflow-hidden">
        {loading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 rounded-md" />
            ))}
          </div>
        ) : (
          <>
            <Table containerClassName="overflow-hidden" className="w-full whitespace-nowrap text-xs">
              <TableHeader>
                <TableRow className="border-b border-border/70 bg-muted/20">
                  <SortHead col="runId"     label="Run ID"      align="left" />
                  <SortHead col="startedAt" label="Date & Time" align="left" />
                  <TableHead className="whitespace-nowrap px-2 py-2.5 text-xs font-semibold text-muted-foreground text-left">
                    Configuration
                  </TableHead>
                  <SortHead col="total"     label="Total Tests" align="center" />
                  <SortHead col="passed"    label="Passed"      align="center" />
                  <SortHead col="failed"    label="Failed"      align="center" />
                  <SortHead col="skipped"   label="Skipped"     align="center" />
                  <SortHead col="passRate"  label="Pass Rate"   align="center" />
                  <SortHead col="duration"  label="Duration"    align="center" />
                  <TableHead className="whitespace-nowrap px-2 py-2.5 text-xs font-semibold text-muted-foreground text-center">
                    Status
                  </TableHead>
                  <TableHead className="whitespace-nowrap px-2 py-2.5 text-xs font-semibold text-muted-foreground text-center">
                    Report
                  </TableHead>
                  <TableHead className="whitespace-nowrap px-2 py-2.5 text-xs font-semibold text-muted-foreground text-right pr-3">
                    Download
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {paginatedRuns.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={12} className="h-24 text-center text-muted-foreground whitespace-nowrap">
                      No validation runs recorded yet.
                    </TableCell>
                  </TableRow>
                ) : (
                  paginatedRuns.map((run) => {
                    const rate = passRate(run);
                    const rateColour =
                      rate >= 80 ? "text-success font-semibold" :
                      rate >= 50 ? "text-amber-500 font-semibold" :
                      "text-destructive font-semibold";
                    const skippedCount =
                      run.skipped ?? (run.results?.filter((r) => r.status === "skipped").length ?? 0);
                    return (
                      <TableRow key={run.runId} className="hover:bg-muted/30 transition-colors border-b border-border/40">
                        {/* 1. Run ID */}
                        <TableCell className="whitespace-nowrap px-2 py-2.5 font-mono text-xs font-bold text-primary">
                          {run.runId}
                        </TableCell>

                        {/* 2. Date & Time */}
                        <TableCell className="whitespace-nowrap px-2 py-2.5 text-xs text-foreground/90">
                          {formatDateTime(run.startedAt)}
                        </TableCell>

                        {/* 3. Configuration */}
                        <TableCell className="whitespace-nowrap px-2 py-2.5 font-mono text-xs text-foreground/90">
                          {run.config}
                        </TableCell>

                        {/* 4. Total Tests */}
                        <TableCell className="whitespace-nowrap px-2 py-2.5 text-center tabular-nums text-xs font-medium text-foreground">
                          {run.total}
                        </TableCell>

                        {/* 5. Passed */}
                        <TableCell className="whitespace-nowrap px-2 py-2.5 text-center tabular-nums text-xs font-bold text-emerald-600 dark:text-emerald-400">
                          {run.passed}
                        </TableCell>

                        {/* 6. Failed */}
                        <TableCell className={`whitespace-nowrap px-2 py-2.5 text-center tabular-nums text-xs font-bold ${run.failed > 0 ? "text-destructive" : "text-destructive/80"}`}>
                          {run.failed}
                        </TableCell>

                        {/* 7. Skipped */}
                        <TableCell className="whitespace-nowrap px-2 py-2.5 text-center tabular-nums text-xs text-muted-foreground">
                          {skippedCount}
                        </TableCell>

                        {/* 8. Pass Rate */}
                        <TableCell className={`whitespace-nowrap px-2 py-2.5 text-center tabular-nums text-xs ${rateColour}`}>
                          {run.total > 0 ? `${rate}%` : "—"}
                        </TableCell>

                        {/* 9. Duration */}
                        <TableCell className="whitespace-nowrap px-2 py-2.5 text-center text-xs tabular-nums text-foreground/90">
                          {run.duration || "—"}
                        </TableCell>

                        {/* 10. Status */}
                        <TableCell className="whitespace-nowrap px-2 py-2.5 text-center">
                          <StatusBadge run={run} />
                        </TableCell>

                        {/* 11. Report */}
                        <TableCell className="whitespace-nowrap px-2 py-2.5 text-center">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setActive(run)}
                            className="h-7 gap-1 px-2 text-xs text-foreground/85 hover:text-foreground whitespace-nowrap"
                          >
                            <Eye className="size-3.5" /> View
                          </Button>
                        </TableCell>

                        {/* 12. Download */}
                        <TableCell className="whitespace-nowrap px-2 py-2.5 text-right pr-3">
                          <ExportButton runId={run.runId} />
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>

            {/* Pagination footer */}
            {totalItems > 0 && (
              <div className="flex flex-col sm:flex-row items-center justify-between gap-4 border-t border-border/70 px-5 py-3 text-xs text-muted-foreground bg-card">
                <div className="whitespace-nowrap">
                  Showing <span className="font-semibold text-foreground">{startIndex + 1}–{endIndex}</span> of{" "}
                  <span className="font-semibold text-foreground">{totalItems}</span> validations
                </div>
                <div className="flex items-center gap-1.5 whitespace-nowrap">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 px-2.5 text-xs"
                    disabled={validCurrentPage === 1}
                    onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                  >
                    Previous
                  </Button>
                  {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                    <Button
                      key={p}
                      variant={validCurrentPage === p ? "default" : "outline"}
                      size="sm"
                      className={`size-8 p-0 text-xs ${validCurrentPage === p ? "bg-primary text-primary-foreground font-semibold" : "text-muted-foreground"}`}
                      onClick={() => setCurrentPage(p)}
                    >
                      {p}
                    </Button>
                  ))}
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-8 px-2.5 text-xs"
                    disabled={validCurrentPage === totalPages}
                    onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </Card>

      <ReportModal run={active} onClose={() => setActive(null)} />
    </div>
  );
}
