/**
 * run-context.tsx — Global state for active test runs and discovery sessions.
 *
 * Holds WebSocket connections and log streams at the app root level so that
 * navigating between tabs does NOT kill an in-progress run or discovery.
 */
import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { type LogLevel, type LogLine } from "@/lib/api-client";

const API = "http://localhost:8000";

// ── Types ────────────────────────────────────────────────────────────────────

export interface ActiveRun {
  runId: string;
  config: string;
  selectedIds: string[];
  logs: LogLine[];
  done: number;
  total: number;
  running: boolean;
}

export interface ActiveDiscovery {
  discId: string;
  url: string;
  name: string;
  logs: LogLine[];
  running: boolean;
  finished: boolean;
}

interface RunContextValue {
  activeRun: ActiveRun | null;
  activeDiscovery: ActiveDiscovery | null;
  startRun: (
    config: string,
    selectedIds: string[],
    excelFile: string,
    testMetadata: Record<string, unknown>[],
    onStart?: (runId: string) => void
  ) => Promise<void>;
  stopRun: () => void;
  clearRun: () => void;
  startDiscovery: (url: string, name: string, onDone?: () => void) => Promise<void>;
  stopDiscovery: () => void;
  clearDiscovery: () => void;
}

// ── Context ──────────────────────────────────────────────────────────────────

const RunContext = createContext<RunContextValue | null>(null);

export function useRunContext() {
  const ctx = useContext(RunContext);
  if (!ctx) throw new Error("useRunContext must be used inside RunProvider");
  return ctx;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function now() {
  return new Date().toLocaleTimeString("en-GB", { hour12: false });
}

// ── Provider ─────────────────────────────────────────────────────────────────

export function RunProvider({ children }: { children: ReactNode }) {
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(null);
  const [activeDiscovery, setActiveDiscovery] = useState<ActiveDiscovery | null>(null);

  const runWsRef = useRef<WebSocket | null>(null);
  const discWsRef = useRef<WebSocket | null>(null);
  const finishedTcIds = useRef<Set<string>>(new Set());

  // ── Run ──────────────────────────────────────────────────────────────────

  const startRun = useCallback(async (
    config: string,
    selectedIds: string[],
    excelFile: string,
    testMetadata: Record<string, unknown>[],
    onStart?: (runId: string) => void,
  ) => {
    runWsRef.current?.close();
    finishedTcIds.current = new Set();

    const res = await fetch(`${API}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        config,
        selected_ids: selectedIds,
        excel_file: excelFile,
        test_metadata: testMetadata,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err as { detail?: string }).detail || `HTTP ${res.status}`);
    }
    const { runId } = (await res.json()) as { runId: string };

    onStart?.(runId);

    setActiveRun({
      runId,
      config,
      selectedIds,
      logs: [{ level: "INFO", text: `Run ${runId} started · Config: ${config}`, time: now() }],
      done: 0,
      total: selectedIds.length,
      running: true,
    });

    const ws = new WebSocket(`ws://localhost:8000/ws/logs/${runId}`);
    runWsRef.current = ws;

    let activeTcId: string | null = selectedIds.length > 0 ? (selectedIds[0] ?? null) : null;

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data as string) as LogLine;
      const text = msg.text || "";
      const level = msg.level || "INFO";

      setActiveRun((prev) => {
        if (!prev) return prev;

        const startMatch = /---\s*Starting\s+(TC-[A-Za-z0-9_-]+)|\[(?:chromium-)?(TC-[A-Za-z0-9_-]+)/i.exec(text);
        if (startMatch) {
          const newTc = (startMatch[1] || startMatch[2] || "").trim();
          if (activeTcId && activeTcId !== newTc && prev.selectedIds.includes(activeTcId)) {
            finishedTcIds.current.add(activeTcId);
          }
          if (prev.selectedIds.includes(newTc)) activeTcId = newTc;
        }

        const isPassFail = level === "PASS" || level === "FAIL" || /\b(PASSED|FAILED)\b/i.test(text);
        if (isPassFail) {
          const direct = /(TC-[A-Za-z0-9_-]+)/i.exec(text);
          const tcToMark = direct && prev.selectedIds.includes(direct[1] ?? "") ? direct[1]! : activeTcId;
          if (tcToMark && prev.selectedIds.includes(tcToMark)) finishedTcIds.current.add(tcToMark);
        }

        if (/STEP(?:5|6|7)_END/i.test(text) && activeTcId && prev.selectedIds.includes(activeTcId)) {
          finishedTcIds.current.add(activeTcId);
        }

        return {
          ...prev,
          logs: [...prev.logs, { level: msg.level, text: msg.text, time: now() }],
          done: finishedTcIds.current.size,
        };
      });
    };

    ws.onclose = () => {
      setActiveRun((prev) =>
        prev ? { ...prev, running: false, done: prev.total } : prev
      );
    };

    ws.onerror = () => {
      setActiveRun((prev) =>
        prev ? { ...prev, running: false } : prev
      );
    };
  }, []);

  const stopRun = useCallback(() => {
    runWsRef.current?.close();
    setActiveRun((prev) => (prev ? { ...prev, running: false } : prev));
  }, []);

  const clearRun = useCallback(() => {
    runWsRef.current?.close();
    setActiveRun(null);
    finishedTcIds.current = new Set();
  }, []);

  // ── Discovery ─────────────────────────────────────────────────────────────

  const startDiscovery = useCallback(async (url: string, name: string, onDone?: () => void) => {
    discWsRef.current?.close();

    const res = await fetch(`${API}/discover`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url.trim(), name: name.trim() }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Unknown error" }));
      throw new Error((err as { detail?: string }).detail || `HTTP ${res.status}`);
    }
    const { discId } = (await res.json()) as { discId: string };

    setActiveDiscovery({
      discId,
      url,
      name,
      logs: [{ level: "INFO", text: `Discovery ${discId} started`, time: now() }],
      running: true,
      finished: false,
    });

    const ws = new WebSocket(`ws://localhost:8000/ws/discover/${discId}`);
    discWsRef.current = ws;

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data as string) as { level?: LogLevel; text?: string };
      setActiveDiscovery((prev) =>
        prev
          ? {
              ...prev,
              logs: [
                ...prev.logs,
                { level: (msg.level ?? "INFO") as LogLevel, text: msg.text ?? "", time: now() },
              ],
            }
          : prev
      );
    };

    ws.onclose = () => {
      setActiveDiscovery((prev) =>
        prev ? { ...prev, running: false, finished: true } : prev
      );
      onDone?.();
    };

    ws.onerror = () => {
      setActiveDiscovery((prev) => (prev ? { ...prev, running: false } : prev));
    };
  }, []);

  const stopDiscovery = useCallback(() => {
    discWsRef.current?.close();
    setActiveDiscovery((prev) => (prev ? { ...prev, running: false } : prev));
  }, []);

  const clearDiscovery = useCallback(() => {
    discWsRef.current?.close();
    setActiveDiscovery(null);
  }, []);

  return (
    <RunContext.Provider
      value={{
        activeRun,
        activeDiscovery,
        startRun,
        stopRun,
        clearRun,
        startDiscovery,
        stopDiscovery,
        clearDiscovery,
      }}
    >
      {children}
    </RunContext.Provider>
  );
}
