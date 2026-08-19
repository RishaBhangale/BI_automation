import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { ArrowDown, Play, Square } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fetchConfigs, type LogLevel, type LogLine } from "@/lib/api-client";
import { useQuery } from "@tanstack/react-query";

const API = "http://localhost:8000";

export const Route = createFileRoute("/run")({
  head: () => ({
    meta: [
      { title: "Run Tests — Automated BI Testing - Validation" },
      {
        name: "description",
        content: "Pick a dashboard config, select scenarios and stream live validation logs.",
      },
      { property: "og:title", content: "Run Tests — Automated BI Testing - Validation" },
      { property: "og:description", content: "Select scenarios and stream live validation logs." },
    ],
  }),
  component: RunTests,
});

const levelClass: Record<LogLevel, string> = {
  PASS: "text-log-pass",
  FAIL: "text-log-fail",
  INFO: "text-log-info",
  STEP: "text-log-step font-bold",
  ERROR: "text-log-error",
};

function now() {
  return new Date().toLocaleTimeString("en-GB", { hour12: false });
}

// ── Types ────────────────────────────────────────────────────────────────────

type ExcelFile = { filename: string };
type TestCaseRow = Record<string, string | null>;

// ── API helpers ──────────────────────────────────────────────────────────────

async function fetchExcelFiles(): Promise<ExcelFile[]> {
  const r = await fetch(`${API}/test-cases/files`);
  if (!r.ok) throw new Error("Failed to fetch Excel files");
  return r.json();
}

async function fetchTestCasesForExcel(source: string): Promise<TestCaseRow[]> {
  const url = source ? `${API}/test-cases?source=${encodeURIComponent(source)}` : `${API}/test-cases`;
  const r = await fetch(url);
  if (!r.ok) throw new Error("Failed to fetch test cases");
  return r.json();
}

// ── Component ────────────────────────────────────────────────────────────────

function RunTests() {
  const { data: configsData, isLoading: configsLoading } = useQuery({
    queryKey: ["configs"],
    queryFn: fetchConfigs,
  });
  const configFiles = configsData ?? [];

  // Excel file selection
  const { data: excelFiles = [], isLoading: excelLoading } = useQuery({
    queryKey: ["excel-files"],
    queryFn: fetchExcelFiles,
  });
  const [selectedExcel, setSelectedExcel] = useState("");
  useEffect(() => {
    if (excelFiles.length > 0 && !selectedExcel) {
      setSelectedExcel(excelFiles[0]!.filename);
    }
  }, [excelFiles, selectedExcel]);

  // Load test cases from selected Excel
  const { data: testCasesData = [], isLoading: tcLoading } = useQuery({
    queryKey: ["test-cases", selectedExcel],
    queryFn: () => fetchTestCasesForExcel(selectedExcel),
    enabled: !!selectedExcel,
  });

  const testCases = testCasesData
    .map((row) => ({
      testId: String(row["Test ID"] ?? ""),
      scenario: String(row["Scenario Name"] ?? ""),
      _row: row,
    }))
    .filter((tc) => tc.testId);

  const loading = configsLoading || excelLoading || tcLoading;

  const [config, setConfig] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [logs, setLogs] = useState<LogLine[]>([]);
  // done = number of unique TC IDs that have received PASSED/FAILED
  const [done, setDone] = useState(0);
  const finishedTcIds = useRef<Set<string>>(new Set());
  const [running, setRunning] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const disconnectRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (configFiles.length > 0 && !config) {
      setConfig(configFiles[0]!.file);
    }
  }, [configFiles, config]);

  useEffect(() => {
    if (testCases.length > 0 && selected.length === 0 && !loading) {
      setSelected(testCases.map((t) => t.testId));
    }
  }, [testCases, selected.length, loading]);

  // Reset selections when Excel changes
  useEffect(() => {
    setSelected([]);
    finishedTcIds.current = new Set();
    setDone(0);
  }, [selectedExcel]);

  const [isAtBottom, setIsAtBottom] = useState(true);
  const userScrolledUpRef = useRef(false);

  const handleWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    if (e.deltaY < 0) {
      userScrolledUpRef.current = true;
      setIsAtBottom(false);
    } else if (e.deltaY > 0) {
      const el = logRef.current;
      if (el && el.scrollHeight - el.scrollTop - el.clientHeight <= 80) {
        userScrolledUpRef.current = false;
        setIsAtBottom(true);
      }
    }
  };

  const handleScroll = () => {
    const el = logRef.current;
    if (!el) return;
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 80;
    if (isNearBottom) {
      userScrolledUpRef.current = false;
      setIsAtBottom(true);
    } else if (userScrolledUpRef.current) {
      setIsAtBottom(false);
    }
  };

  const scrollToBottom = () => {
    userScrolledUpRef.current = false;
    setIsAtBottom(true);
    if (logRef.current) {
      logRef.current.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
    }
  };

  useEffect(() => {
    if (!userScrolledUpRef.current && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  const allSelected = selected.length === testCases.length && testCases.length > 0;

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  const push = (level: LogLevel, text: string) =>
    setLogs((l) => [...l, { level, text, time: now() }]);

  const start = async () => {
    if (selected.length === 0) { toast.error("Select at least one test case"); return; }
    setLogs([]);
    setDone(0);
    finishedTcIds.current = new Set();
    userScrolledUpRef.current = false;
    setIsAtBottom(true);
    setRunning(true);

    // Gather metadata for selected rows (for Conditions view in History)
    const metaMap = Object.fromEntries(
      testCases
        .filter((tc) => selected.includes(tc.testId))
        .map((tc) => [tc.testId, tc._row])
    );

    try {
      const res = await fetch(`${API}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          config,
          selected_ids: selected,
          excel_file: selectedExcel,
          test_metadata: Object.values(metaMap),
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const { runId } = await res.json();
      toast.success(`Run started · ${selected.length} scenarios`);
      push("INFO", `Run ID: ${runId} · Config: ${config} · Excel: ${selectedExcel}`);

      // WebSocket log streaming
      const ws = new WebSocket(`ws://localhost:8000/ws/logs/${runId}`);
      disconnectRef.current = () => ws.close();

      let activeTcId: string | null = selected.length > 0 ? selected[0] : null;

      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        const line = msg as LogLine;
        push(line.level, line.text);

        const text = line.text || "";
        const level = line.level || "";

        // 1. Detect start of a test case
        const startMatch = /---\s*Starting\s+(TC-[A-Za-z0-9_-]+)|\[(?:(?:chromium|firefox|webkit)-)?(TC-[A-Za-z0-9_-]+)/i.exec(text);
        if (startMatch) {
          const newTc = (startMatch[1] || startMatch[2] || "").trim();
          if (activeTcId && activeTcId !== newTc && selected.includes(activeTcId)) {
            finishedTcIds.current.add(activeTcId);
            setDone(finishedTcIds.current.size);
          }
          if (selected.includes(newTc)) {
            activeTcId = newTc;
          }
        }

        // 2. Detect test pass/fail line
        const isPassFail = level === "PASS" || level === "FAIL" || /\b(PASSED|FAILED)\b/i.test(text);
        if (isPassFail) {
          const directMatch = /(TC-[A-Za-z0-9_-]+)/i.exec(text);
          if (directMatch && selected.includes(directMatch[1])) {
            finishedTcIds.current.add(directMatch[1]);
            setDone(finishedTcIds.current.size);
          } else if (activeTcId && selected.includes(activeTcId)) {
            finishedTcIds.current.add(activeTcId);
            setDone(finishedTcIds.current.size);
          }
        }

        // 3. Detect end of step execution
        if (/STEP(?:5|6|7)_END/i.test(text) && activeTcId && selected.includes(activeTcId)) {
          finishedTcIds.current.add(activeTcId);
          setDone(finishedTcIds.current.size);
        }
      };
      ws.onclose = () => {
        setRunning(false);
        setDone(selected.length);
        toast.success("Run finished");
        push("INFO", "Run complete. History updated.");
      };
      ws.onerror = () => {
        setRunning(false);
        push("ERROR", "WebSocket disconnected");
      };
    } catch (e: unknown) {
      setRunning(false);
      toast.error("Failed to start run: " + (e instanceof Error ? e.message : String(e)));
    }
  };

  const stop = () => {
    disconnectRef.current?.();
    setRunning(false);
    push("ERROR", "Run aborted by user.");
    toast.warning("Run stopped");
  };

  return (
    <div className="grid gap-6 p-6 lg:grid-cols-[2fr_3fr] lg:p-8">
      <Card className="h-fit border-border/70 p-5">
        <div>
          <h1 className="text-lg font-semibold">Run Tests</h1>
          <p className="text-sm text-muted-foreground">Choose a config and the scenarios to validate.</p>
        </div>

        {/* Dashboard Config */}
        <div className="space-y-2 mt-4">
          <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Dashboard Config
          </label>
          <Select value={config} onValueChange={setConfig}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {configFiles.map((c) => (
                <SelectItem key={c.file} value={c.file}>
                  {c.file}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Test Cases Excel selector */}
        <div className="space-y-2 mt-4">
          <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Test Cases Excel
          </label>
          <Select value={selectedExcel} onValueChange={(v) => setSelectedExcel(v)}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Select Excel file…" />
            </SelectTrigger>
            <SelectContent>
              {excelFiles.map((f) => (
                <SelectItem key={f.filename} value={f.filename}>
                  {f.filename}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Test case checkboxes */}
        <div className="space-y-3 mt-4">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Test Cases ({selected.length}/{testCases.length})
            </label>
            <button
              className="text-xs text-primary hover:underline"
              onClick={() => setSelected(allSelected ? [] : testCases.map((t) => t.testId))}
            >
              {allSelected ? "Clear all" : "Select all"}
            </button>
          </div>
          <div className="max-h-[360px] space-y-1 overflow-y-auto rounded-lg border border-border/70 p-2">
            {loading
              ? Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-11 rounded-md" />)
              : testCases.map((tc) => (
                  <label
                    key={tc.testId}
                    className="flex cursor-pointer items-start gap-3 rounded-md p-2 hover:bg-muted/60"
                  >
                    <Checkbox
                      checked={selected.includes(tc.testId)}
                      onCheckedChange={() => toggle(tc.testId)}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="font-mono text-xs text-primary">{tc.testId}</span>
                      <span className="block text-sm">{tc.scenario}</span>
                    </span>
                  </label>
                ))}
          </div>
        </div>

        <Button className="w-full mt-4" size="lg" onClick={start} disabled={running}>
          <Play className="size-4" /> Run Selected Tests
        </Button>
      </Card>

      {/* Log panel */}
      <Card className="gap-4 border-border/70 bg-panel p-5">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
              <span>Progress</span>
              <span className="tabular-nums">
                {done}/{selected.length} tests completed
              </span>
            </div>
            <Progress value={selected.length ? (done / selected.length) * 100 : 0} />
          </div>
          {running ? (
            <Button variant="destructive" onClick={stop}>
              <Square className="size-4" /> Stop Run
            </Button>
          ) : null}
        </div>

        <div className="relative mt-4">
          <div
            ref={logRef}
            onScroll={handleScroll}
            onWheel={handleWheel}
            className="h-[540px] overflow-y-auto rounded-lg border border-border/70 bg-background/60 p-4 font-mono text-xs leading-relaxed"
          >
            {logs.length === 0 ? (
              <p className="text-log-info">// no active run — logs will stream here</p>
            ) : (
              logs.map((l, i) => (
                <div key={i} className={levelClass[l.level]}>
                  <span className="text-log-info">{l.time} </span>
                  <span className="opacity-90">[{l.level}]</span> {l.text}
                </div>
              ))
            )}
          </div>
          {!isAtBottom && logs.length > 0 && (
            <button
              type="button"
              onClick={scrollToBottom}
              className="absolute bottom-4 right-4 flex items-center gap-1.5 rounded-full bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground shadow-lg transition-all hover:bg-primary/90 hover:scale-105 cursor-pointer z-10 animate-in fade-in"
            >
              <ArrowDown className="size-3.5" /> Latest logs
            </button>
          )}
        </div>
      </Card>
    </div>
  );
}