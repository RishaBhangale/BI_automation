import { createFileRoute } from "@tanstack/react-router";
import { useState, useRef, useEffect } from "react";
import { Download, Eye, FileText, Globe, ChevronDown } from "lucide-react";
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
      { property: "og:title", content: "Test History — Automated BI Testing - Validation" },
      { property: "og:description", content: "Browse past validation runs and export reports." },
    ],
  }),
  component: History,
});

// ── Export split-button ──────────────────────────────────────────────────────

function ExportButton({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
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
      <Button
        variant="ghost"
        size="sm"
        disabled={busy}
        onClick={() => setOpen((o) => !o)}
        aria-label="Export options"
      >
        <Download className="size-4" />
        {busy ? "Exporting…" : "Export"}
        <ChevronDown className="size-3.5 ml-1" />
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-40 rounded-lg border border-border bg-card shadow-lg">
          <button
            className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-muted/60 rounded-t-lg"
            onClick={() => doExport("pdf")}
          >
            <FileText className="size-4 text-muted-foreground" /> Export as PDF
          </button>
          <button
            className="flex w-full items-center gap-2 px-3 py-2 text-sm hover:bg-muted/60 rounded-b-lg"
            onClick={() => doExport("html")}
          >
            <Globe className="size-4 text-muted-foreground" /> Export as HTML
          </button>
        </div>
      )}
    </div>
  );
}

// ── History page ─────────────────────────────────────────────────────────────

function History() {
  const { data, isLoading: loading } = useQuery({ queryKey: ["runs"], queryFn: fetchRuns });
  const runs = data ?? [];
  const [active, setActive] = useState<Run | null>(null);

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
                <TableHead>Run ID</TableHead>
                <TableHead>Date/Time</TableHead>
                <TableHead>Config Used</TableHead>
                <TableHead className="text-right">Total</TableHead>
                <TableHead className="text-right">Passed</TableHead>
                <TableHead className="text-right">Failed</TableHead>
                <TableHead>Duration</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((run) => (
                <TableRow key={run.runId}>
                  <TableCell className="font-mono text-xs text-primary">{run.runId}</TableCell>
                  <TableCell className="text-sm">{new Date(run.startedAt).toLocaleString()}</TableCell>
                  <TableCell className="font-mono text-xs">{run.config}</TableCell>
                  <TableCell className="text-right tabular-nums">{run.total}</TableCell>
                  <TableCell className="text-right tabular-nums text-success">{run.passed}</TableCell>
                  <TableCell className="text-right tabular-nums text-destructive">{run.failed}</TableCell>
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
              ))}
            </TableBody>
          </Table>
        )}
      </Card>

      <ReportModal run={active} onClose={() => setActive(null)} />
    </div>
  );
}
