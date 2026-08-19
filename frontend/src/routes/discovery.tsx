import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { ArrowDown, Play, Search } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import type { LogLevel } from "@/lib/api-client";

const API = "http://localhost:8000";

export const Route = createFileRoute("/discovery")({
  head: () => ({
    meta: [
      { title: "Discovery — Automated BI Testing - Validation" },
      {
        name: "description",
        content: "Run Power BI dashboard discovery to auto-generate YAML test configs.",
      },
    ],
  }),
  component: DiscoveryPage,
});

type DiscResult = { file: string; path: string; sizeBytes: number; modifiedAt: number };

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

function DiscoveryPage() {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [running, setRunning] = useState(false);
  const [finished, setFinished] = useState(false);
  const [logs, setLogs] = useState<{ level: LogLevel; text: string; time: string }[]>([]);
  const [results, setResults] = useState<DiscResult[]>([]);
  const logRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Load existing YAML configs on mount
  useEffect(() => {
    fetch(`${API}/discover/results`)
      .then((r) => r.json())
      .then((data) => setResults(data))
      .catch(() => {});
  }, [finished]);

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

  const push = (level: LogLevel, text: string) =>
    setLogs((l) => [...l, { level, text, time: now() }]);

  const start = async () => {
    if (!url.trim()) { toast.error("Dashboard URL is required"); return; }
    if (!name.trim()) { toast.error("Config name is required"); return; }

    setLogs([]);
    setFinished(false);
    userScrolledUpRef.current = false;
    setIsAtBottom(true);
    setRunning(true);

    try {
      const res = await fetch(`${API}/discover`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim(), name: name.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Unknown error" }));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const { discId } = await res.json();
      toast.success(`Discovery ${discId} started`);
      push("INFO", `Discovery ID: ${discId}`);

      // Open WebSocket to stream logs
      const ws = new WebSocket(`ws://localhost:8000/ws/discover/${discId}`);
      wsRef.current = ws;

      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        push((msg.level as LogLevel) || "INFO", msg.text || "");
      };
      ws.onclose = () => {
        setRunning(false);
        setFinished(true);
        toast.success("Discovery complete");
        push("INFO", "Discovery finished. YAML config listed below.");
      };
      ws.onerror = () => {
        setRunning(false);
        toast.error("WebSocket error during discovery");
      };
    } catch (e: unknown) {
      setRunning(false);
      toast.error("Failed: " + (e instanceof Error ? e.message : String(e)));
    }
  };

  return (
    <div className="space-y-6 p-6 lg:p-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Discovery</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Auto-discover a Power BI dashboard to generate a YAML test configuration.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_1.6fr]">
        {/* Left panel: inputs */}
        <Card className="h-fit border-border/70 p-5 space-y-4">
          <div className="space-y-2">
            <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Dashboard URL <span className="text-destructive">*</span>
            </label>
            <Input
              placeholder="https://app.powerbi.com/view?r=..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={running}
            />
          </div>

          <div className="space-y-2">
            <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Config Name <span className="text-destructive">*</span>
            </label>
            <Input
              placeholder="e.g. Sales Dashboard"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={running}
            />
            <p className="text-[11px] text-muted-foreground">
              Saved as <span className="font-mono">dashboard_configs/&lt;name&gt;.yaml</span>
            </p>
          </div>

          <Button className="w-full" size="lg" onClick={start} disabled={running || !url || !name}>
            {running ? (
              <><span className="animate-pulse">●</span> Running Discovery…</>
            ) : (
              <><Play className="size-4" /> Run Discovery</>
            )}
          </Button>
        </Card>

        {/* Right panel: log stream */}
        <Card className="border-border/70 bg-panel p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">Live Output</h2>
            {running && <Progress value={undefined} className="w-24 animate-pulse" />}
          </div>
          <div className="relative">
            <div
              ref={logRef}
              onScroll={handleScroll}
              onWheel={handleWheel}
              className="h-[400px] overflow-y-auto rounded-lg border border-border/70 bg-background/60 p-4 font-mono text-xs leading-relaxed"
            >
              {logs.length === 0 ? (
                <p className="text-muted-foreground">// logs will stream here once discovery starts</p>
              ) : (
                logs.map((l, i) => (
                  <div key={i} className={levelClass[l.level] || "text-foreground"}>
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

      {/* Generated configs list */}
      <div>
        <h2 className="mb-3 text-sm font-semibold flex items-center gap-2">
          <Search className="size-4" /> Generated Configs ({results.length})
        </h2>
        {results.length === 0 ? (
          <p className="text-sm text-muted-foreground">No YAML configs generated yet.</p>
        ) : (
          <Card className="border-border/70 p-0 divide-y divide-border/60">
            {results.map((r) => (
              <div key={r.file} className="flex items-center justify-between px-4 py-3 text-sm">
                <span className="font-mono text-primary">{r.file}</span>
                <span className="text-xs text-muted-foreground">
                  {(r.sizeBytes / 1024).toFixed(1)} KB ·{" "}
                  {new Date(r.modifiedAt * 1000).toLocaleString()}
                </span>
              </div>
            ))}
          </Card>
        )}
      </div>
    </div>
  );
}
