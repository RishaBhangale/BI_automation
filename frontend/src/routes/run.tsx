import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { ArrowDown, ChevronDown, ChevronUp, Play, Search, Square } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
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
import { useRunContext } from "@/context/run-context";

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

// ── Log Panel (shared) ───────────────────────────────────────────────────────

function LogPanel({
  logs,
  mode,
}: {
  logs: LogLine[];
  mode: "run" | "discovery";
}) {
  const logRef = useRef<HTMLDivElement>(null);
  const userScrolledUpRef = useRef(false);
  const [isAtBottom, setIsAtBottom] = useState(true);

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

  // Reset scroll state when mode switches
  useEffect(() => {
    userScrolledUpRef.current = false;
    setIsAtBottom(true);
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [mode]);

  useEffect(() => {
    if (!userScrolledUpRef.current && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  const emptyMsg =
    mode === "discovery"
      ? "// logs will stream here once discovery starts"
      : "// no active run — logs will stream here";

  return (
    <div className="relative">
      <div
        ref={logRef}
        onScroll={handleScroll}
        onWheel={handleWheel}
        className="h-[540px] overflow-y-auto rounded-lg border border-border/70 bg-background/60 p-4 font-mono text-xs leading-relaxed"
      >
        {logs.length === 0 ? (
          <p className="text-log-info">{emptyMsg}</p>
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
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

function RunTests() {
  const queryClient = useQueryClient();
  const { activeRun, activeDiscovery, startRun, stopRun, startDiscovery, stopDiscovery } = useRunContext();

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
  }, [selectedExcel]);

  const allSelected = selected.length === testCases.length && testCases.length > 0;

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  // ── Log panel mode — switches between "run" and "discovery" ──────────────
  // Keeps last active mode when neither is running
  const [logMode, setLogMode] = useState<"run" | "discovery">("run");
  const displayLogs =
    logMode === "discovery"
      ? (activeDiscovery?.logs ?? [])
      : (activeRun?.logs ?? []);

  // Auto-switch mode when a new run/discovery starts
  useEffect(() => {
    if (activeDiscovery?.running) setLogMode("discovery");
  }, [activeDiscovery?.running]);
  useEffect(() => {
    if (activeRun?.running) setLogMode("run");
  }, [activeRun?.running]);

  // ── Discovery panel state ────────────────────────────────────────────────
  const [discOpen, setDiscOpen] = useState(false);
  const [discUrl, setDiscUrl] = useState("");
  const [discName, setDiscName] = useState("");

  const handleStartDiscovery = async () => {
    if (!discUrl.trim()) { toast.error("Dashboard URL is required"); return; }
    if (!discName.trim()) { toast.error("Config name is required"); return; }
    setLogMode("discovery");
    try {
      await startDiscovery(discUrl, discName, () => {
        // Refresh config dropdown after discovery finishes
        queryClient.invalidateQueries({ queryKey: ["configs"] });
        toast.success("Discovery complete — config dropdown refreshed");
      });
      toast.success("Discovery started");
    } catch (e: unknown) {
      toast.error("Discovery failed: " + (e instanceof Error ? e.message : String(e)));
    }
  };

  // ── Run ──────────────────────────────────────────────────────────────────
  const handleStartRun = async () => {
    if (selected.length === 0) { toast.error("Select at least one test case"); return; }
    setLogMode("run");

    const metaMap = Object.fromEntries(
      testCases
        .filter((tc) => selected.includes(tc.testId))
        .map((tc) => [tc.testId, tc._row])
    );

    try {
      await startRun(
        config,
        selected,
        selectedExcel,
        Object.values(metaMap),
        (runId) => toast.success(`Run started · ${selected.length} scenarios · ${runId}`),
      );
    } catch (e: unknown) {
      toast.error("Failed to start run: " + (e instanceof Error ? e.message : String(e)));
    }
  };

  const running = !!activeRun?.running;
  const discRunning = !!activeDiscovery?.running;

  // Progress derived from context
  const done = activeRun?.done ?? 0;
  const total = activeRun?.total ?? selected.length;

  return (
    <div className="grid gap-6 p-6 lg:grid-cols-[2fr_3fr] lg:p-8">
      <Card className="h-fit border-border/70 p-5">
        <div>
          <h1 className="text-lg font-semibold">Run Tests</h1>
          <p className="text-sm text-muted-foreground">Choose a config and the scenarios to validate.</p>
        </div>

        {/* ── Discovery Panel ──────────────────────────────────────────── */}
        <div className="mt-4 rounded-lg border border-border/60 bg-muted/20">
          <button
            type="button"
            className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-foreground hover:bg-muted/30 rounded-lg transition-colors"
            onClick={() => setDiscOpen((o) => !o)}
          >
            <span className="flex items-center gap-2">
              <Search className="size-3.5 text-muted-foreground" />
              Discover New Dashboard
            </span>
            {discOpen ? <ChevronUp className="size-4 text-muted-foreground" /> : <ChevronDown className="size-4 text-muted-foreground" />}
          </button>

          {discOpen && (
            <div className="space-y-3 px-4 pb-4">
              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Dashboard URL <span className="text-destructive">*</span>
                </label>
                <Input
                  placeholder="https://app.powerbi.com/view?r=..."
                  value={discUrl}
                  onChange={(e) => setDiscUrl(e.target.value)}
                  disabled={discRunning}
                  className="text-xs"
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Config Name <span className="text-destructive">*</span>
                </label>
                <Input
                  placeholder="e.g. Sales Dashboard"
                  value={discName}
                  onChange={(e) => setDiscName(e.target.value)}
                  disabled={discRunning}
                  className="text-xs"
                />
                <p className="text-[11px] text-muted-foreground">
                  Saved as <span className="font-mono">dashboard_configs/&lt;name&gt;.yaml</span>
                </p>
              </div>
              <Button
                className="w-full"
                size="sm"
                variant="secondary"
                onClick={handleStartDiscovery}
                disabled={discRunning || !discUrl || !discName}
              >
                {discRunning ? (
                  <><span className="animate-pulse">●</span> Running Discovery…</>
                ) : (
                  <><Play className="size-3.5" /> Run Discovery</>
                )}
              </Button>
            </div>
          )}
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
            Test Data Excel
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
          <div className="max-h-[320px] space-y-1 overflow-y-auto rounded-lg border border-border/70 p-2">
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

        <Button className="w-full mt-4" size="lg" onClick={handleStartRun} disabled={running}>
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
                {done}/{total} tests completed
              </span>
            </div>
            <Progress value={total ? (done / total) * 100 : 0} />
          </div>
          {running ? (
            <Button variant="destructive" onClick={stopRun}>
              <Square className="size-4" /> Stop Run
            </Button>
          ) : null}
        </div>

        {/* Log mode switcher */}
        <div className="mt-3 flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Log view:</span>
          <button
            className={
              "rounded-md px-2.5 py-1 transition-colors " +
              (logMode === "run"
                ? "bg-primary text-primary-foreground font-medium"
                : "text-muted-foreground hover:bg-muted/60")
            }
            onClick={() => setLogMode("run")}
          >
            Test Run
            {activeRun?.running && <span className="ml-1.5 inline-block size-1.5 rounded-full bg-green-400 animate-pulse" />}
          </button>
          <button
            className={
              "rounded-md px-2.5 py-1 transition-colors " +
              (logMode === "discovery"
                ? "bg-primary text-primary-foreground font-medium"
                : "text-muted-foreground hover:bg-muted/60")
            }
            onClick={() => setLogMode("discovery")}
          >
            Discovery
            {activeDiscovery?.running && <span className="ml-1.5 inline-block size-1.5 rounded-full bg-blue-400 animate-pulse" />}
          </button>
        </div>

        <div className="mt-2">
          <LogPanel logs={displayLogs} mode={logMode} />
        </div>
      </Card>
    </div>
  );
}
