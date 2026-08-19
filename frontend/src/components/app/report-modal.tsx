/**
 * report-modal.tsx — Shared ReportModal and ResultCard used in both
 * the Dashboard (index.tsx) and History page (history.tsx).
 */
import { useState } from "react";
import { ChevronDown, Timer, CheckCircle2, XCircle, Sliders, Layers, FileCode } from "lucide-react";

import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MetricCard } from "@/components/app/metric-card";
import type { Run, TestResult, LogLevel } from "@/lib/api-client";

const levelClass: Record<LogLevel | string, string> = {
  PASS: "text-emerald-600 dark:text-emerald-400",
  PASSED: "text-emerald-600 dark:text-emerald-400",
  FAIL: "text-rose-600 dark:text-rose-400",
  FAILED: "text-rose-600 dark:text-rose-400",
  INFO: "text-muted-foreground",
  STEP: "text-primary font-semibold",
  ERROR: "text-rose-600 dark:text-rose-400",
};

export function ResultCard({ result }: { result: TestResult }) {
  const [stepsOpen, setStepsOpen] = useState(false);
  const [condOpen, setCondOpen] = useState(false);
  const statusStr = (result.status || "").toUpperCase();
  const isPass = statusStr === "PASS" || statusStr === "PASSED";

  // Build conditions list from meta (slicer name→value pairs + KPI + scenario details)
  const meta = (result as any).meta as Record<string, unknown> | undefined;
  const slicers: { name: string; value: string }[] = [];
  let kpiToRead = "";
  let sqlFile = "";
  const otherParams: { label: string; value: string }[] = [];

  if (meta) {
    // 1. Parse slicers from 'Slicer N Name' / 'Slicer N Value' or 'Slicer N' / 'Value N'
    for (let i = 1; i <= 12; i++) {
      const name = (meta[`Slicer ${i} Name`] ?? meta[`Slicer ${i}`]) as string | null | undefined;
      const val = (meta[`Slicer ${i} Value`] ?? meta[`Value ${i}`]) as string | null | undefined;
      if (name && String(name).trim() && String(name).toLowerCase() !== "nan") {
        slicers.push({
          name: String(name).trim(),
          value: val !== null && val !== undefined && String(val).toLowerCase() !== "nan" ? String(val).trim() : "—",
        });
      }
    }

    // 2. Target KPI
    const rawKpi = (meta["KPI to Read"] ?? meta["kpi"]) as string | null | undefined;
    if (rawKpi && String(rawKpi).trim() && String(rawKpi).toLowerCase() !== "nan") {
      kpiToRead = String(rawKpi).trim();
    }

    // 3. SQL Source
    const rawSql = (meta["SQL File Name"] ?? meta["sql_file"]) as string | null | undefined;
    if (rawSql && String(rawSql).trim() && String(rawSql).toLowerCase() !== "nan") {
      sqlFile = String(rawSql).trim();
    }

    // 4. Other columns
    const handledKeys = new Set([
      "Test ID", "Scenario Name", "KPI to Read", "kpi", "SQL File Name", "sql_file"
    ]);
    for (let i = 1; i <= 12; i++) {
      handledKeys.add(`Slicer ${i} Name`);
      handledKeys.add(`Slicer ${i} Value`);
      handledKeys.add(`Slicer ${i}`);
      handledKeys.add(`Value ${i}`);
    }

    Object.entries(meta).forEach(([k, v]) => {
      if (!handledKeys.has(k) && v !== null && v !== undefined && String(v).trim() && String(v).toLowerCase() !== "nan") {
        otherParams.push({ label: k, value: String(v) });
      }
    });
  }

  const rawSteps = (result as any).steps as any[] | undefined;
  const hasSteps = Array.isArray(rawSteps) && rawSteps.length > 0;

  return (
    <Card className="gap-0 border-border/70 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="font-mono text-xs text-primary font-semibold">{result.tc_id}</span>
          <p className="text-sm font-medium">{result.name}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Timer className="size-3" /> {result.duration}
          </span>
          <span
            className={
              "rounded-full px-2.5 py-1 text-xs font-medium " +
              (isPass
                ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                : "bg-rose-500/15 text-rose-600 dark:text-rose-400")
            }
          >
            {statusStr}
          </span>
        </div>
      </div>

      <div className="mt-3 flex gap-4">
        {/* Steps accordion */}
        <button
          onClick={() => setStepsOpen((o) => !o)}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground font-medium transition-colors"
        >
          <ChevronDown className={"size-3.5 transition-transform " + (stepsOpen ? "rotate-180" : "")} />
          Steps {hasSteps ? `(${rawSteps.length})` : ""}
        </button>

        {/* Conditions accordion */}
        {(meta || slicers.length > 0 || kpiToRead) && (
          <button
            onClick={() => setCondOpen((o) => !o)}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground font-medium transition-colors"
          >
            <ChevronDown className={"size-3.5 transition-transform " + (condOpen ? "rotate-180" : "")} />
            Conditions &amp; Criteria
          </button>
        )}
      </div>

      {/* Steps Content */}
      {stepsOpen && (
        <div className="mt-3 space-y-2 rounded-lg border border-border/70 bg-background/50 p-3 text-xs">
          {hasSteps ? (
            rawSteps.map((s: any, i: number) => {
              // Check if structured step object or raw log line
              if (s && typeof s === "object" && ("step_no" in s || "title" in s)) {
                const stepPassed = !s.failed;
                const lines = Array.isArray(s.lines) ? s.lines : [];
                return (
                  <div
                    key={i}
                    className="rounded-md border border-border/60 bg-muted/20 p-2.5 space-y-1.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        {stepPassed ? (
                          <CheckCircle2 className="size-4 text-emerald-500 shrink-0" />
                        ) : (
                          <XCircle className="size-4 text-rose-500 shrink-0" />
                        )}
                        <span className="font-semibold text-foreground">
                          Step {s.step_no ?? i + 1}: {s.title}
                        </span>
                      </div>
                      <span
                        className={
                          "text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded " +
                          (stepPassed
                            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                            : "bg-rose-500/10 text-rose-600 dark:text-rose-400")
                        }
                      >
                        {stepPassed ? "Passed" : "Failed"}
                      </span>
                    </div>

                    {lines.length > 0 && (
                      <div className="font-mono text-[11px] space-y-0.5 pl-6 pt-1 text-muted-foreground">
                        {lines.map((l: any, li: number) => {
                          const level = Array.isArray(l) ? l[0] : l.level || "INFO";
                          const time = Array.isArray(l) ? l[1] : l.time || "";
                          const msg = Array.isArray(l) ? l[2] : l.message || l.text || "";
                          return (
                            <div key={li} className={levelClass[level] || ""}>
                              {time && <span className="opacity-60">{time} </span>}
                              <span className="font-semibold">[{level}]</span> {msg}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              }

              // Flat log line fallback
              return (
                <div key={i} className={`font-mono text-xs ${levelClass[s.level || "INFO"] || ""}`}>
                  <span className="opacity-60">{s.time} </span>[{s.level || "INFO"}] {s.message || s.text}
                </div>
              );
            })
          ) : (
            <div className="text-muted-foreground py-2 text-center">
              No step details recorded in this run. Run tests with live execution to capture step logs.
            </div>
          )}
        </div>
      )}

      {/* Conditions Content */}
      {condOpen && (
        <div className="mt-3 rounded-lg border border-border/70 bg-background/50 p-4 text-xs space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            {/* Target KPI */}
            <div className="rounded-md border border-border/50 bg-muted/30 p-3">
              <div className="flex items-center gap-1.5 font-semibold text-foreground mb-1">
                <Layers className="size-3.5 text-primary" /> Target Metric (KPI)
              </div>
              <p className="font-mono text-sm font-semibold text-primary">
                {kpiToRead || "Grand Total / Default"}
              </p>
            </div>

            {/* SQL Source */}
            <div className="rounded-md border border-border/50 bg-muted/30 p-3">
              <div className="flex items-center gap-1.5 font-semibold text-foreground mb-1">
                <FileCode className="size-3.5 text-primary" /> Source SQL Query
              </div>
              <p className="font-mono text-xs text-muted-foreground">
                {sqlFile ? sqlFile : "Dynamic Query Template (Auto-Generated)"}
              </p>
            </div>
          </div>

          {/* Slicers Table */}
          <div className="rounded-md border border-border/50 bg-muted/30 p-3">
            <div className="flex items-center gap-1.5 font-semibold text-foreground mb-2">
              <Sliders className="size-3.5 text-primary" /> Applied Dashboard Slicers
            </div>
            {slicers.length > 0 ? (
              <div className="grid gap-1.5 sm:grid-cols-2">
                {slicers.map((s, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between rounded border border-border/40 bg-background/60 px-2.5 py-1.5"
                  >
                    <span className="font-medium text-muted-foreground">{s.name}</span>
                    <span className="font-mono font-semibold text-foreground">{s.value}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-muted-foreground italic">
                None (Evaluated on default dashboard state / Grand Total).
              </p>
            )}
          </div>

          {/* Other Parameters */}
          {otherParams.length > 0 && (
            <div className="rounded-md border border-border/50 bg-muted/30 p-3">
              <div className="font-semibold text-foreground mb-1.5">Additional Test Metadata</div>
              <div className="space-y-1">
                {otherParams.map((p, idx) => (
                  <div key={idx} className="flex justify-between py-0.5 border-b border-border/30 last:border-0">
                    <span className="text-muted-foreground">{p.label}</span>
                    <span className="font-mono">{p.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export function ReportModal({ run, onClose }: { run: Run | null; onClose: () => void }) {
  return (
    <Dialog open={!!run} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-4xl">
        {run ? (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center justify-between gap-2 pr-6">
                <span>
                  {run.runId} · <span className="font-mono text-sm font-normal text-muted-foreground">{run.config}</span>
                </span>
              </DialogTitle>
            </DialogHeader>
            <div className="grid gap-3 sm:grid-cols-4 mt-2">
              <MetricCard label="Total" value={run.total} />
              <MetricCard label="Passed" value={run.passed} tone="success" />
              <MetricCard label="Failed" value={run.failed} tone="danger" />
              <MetricCard label="Duration" value={run.duration || "-"} />
            </div>
            {(["failed", "passed"] as const).map((status) => {
              const group = run.results.filter((r) => (r.status || "").toLowerCase() === status);
              if (group.length === 0) return null;
              return (
                <div key={status} className="space-y-2 mt-4">
                  <h3 className="text-sm font-semibold flex items-center gap-2">
                    {status === "failed" ? (
                      <span className="text-rose-600 dark:text-rose-400">Failed Tests ({group.length})</span>
                    ) : (
                      <span className="text-emerald-600 dark:text-emerald-400">Passed Tests ({group.length})</span>
                    )}
                  </h3>
                  {group.map((r) => (
                    <ResultCard key={r.tc_id} result={r} />
                  ))}
                </div>
              );
            })}
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
