/**
 * api-client.ts — Typed API client for the BI Validator backend.
 * All calls go to http://localhost:8000.
 */

export const BASE_URL = "http://localhost:8000";
const WS_URL = BASE_URL.replace("http", "ws");

export type LogLevel = "PASS" | "FAIL" | "INFO" | "STEP" | "ERROR";

export type LogLine = {
  level: LogLevel;
  text: string;
  time: string;
};

export type TestResult = {
  tc_id: string;
  name: string;
  status: "passed" | "failed" | "skipped";
  duration: string;
  error_text?: string;
};

export type Run = {
  runId: string;
  config: string;
  status: "running" | "finished";
  startedAt: string;
  finishedAt: string | null;
  duration: string | null;
  total: number;
  passed: number;
  failed: number;
  results: TestResult[];
};

export type TestCase = Record<string, string | null>;

export type ConfigFile = {
  file: string;
  dashboard: string;
  pages: number;
  driver: string;
  content: string;
};

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, init);
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const fetchRuns = () => apiFetch<Run[]>("/runs");
export const fetchRun = (id: string) => apiFetch<Run>(`/runs/${id}`);

export const startRun = (config: string, selectedIds: string[]) =>
  apiFetch<{ runId: string; message: string }>("/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config, selected_ids: selectedIds }),
  });

export const fetchTestCases = () => apiFetch<TestCase[]>("/test-cases");

export const saveTestCases = (rows: TestCase[]) =>
  apiFetch<{ saved: number }>("/test-cases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows }),
  });

export const fetchConfigs = () => apiFetch<ConfigFile[]>("/configs");

export const saveConfig = (filename: string, content: string) =>
  apiFetch<{ saved: string }>(`/configs/${encodeURIComponent(filename)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });

export async function exportRun(runId: string, format: "pdf" | "html" = "pdf"): Promise<string> {
  const res = await fetch(`${BASE_URL}/export/${runId}?format=${format}`);
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export function connectLogStream(
  runId: string,
  onLine: (line: LogLine) => void,
  onDone: () => void,
): () => void {
  const ws = new WebSocket(`${WS_URL}/ws/logs/${runId}`);
  ws.onmessage = (ev) => {
    try { onLine(JSON.parse(ev.data as string) as LogLine); }
    catch { onLine({ level: "INFO", text: ev.data as string, time: "" }); }
  };
  ws.onclose = () => onDone();
  ws.onerror = () => onDone();
  return () => ws.close();
}

export async function checkBackendHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/health`, { signal: AbortSignal.timeout(2000) });
    return res.ok;
  } catch { return false; }
}
