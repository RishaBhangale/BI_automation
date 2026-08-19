import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Plus, RefreshCw, Save, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useQuery, useQueryClient } from "@tanstack/react-query";

const API = "http://localhost:8000";

export const Route = createFileRoute("/test-cases")({
  head: () => ({
    meta: [
      { title: "Test Cases — Automated BI Testing - Validation" },
      {
        name: "description",
        content: "Edit business scenarios, slicer selections, KPIs and SQL baselines inline.",
      },
    ],
  }),
  component: TestCasesPage,
});

// ── Types ────────────────────────────────────────────────────────────────────

type ExcelFile = { filename: string; sizeBytes: number; modifiedAt: number };
// Row is a generic object keyed by column name
type Row = Record<string, string | null>;

// ── API helpers ──────────────────────────────────────────────────────────────

async function fetchExcelFiles(): Promise<ExcelFile[]> {
  const r = await fetch(`${API}/test-cases/files`);
  if (!r.ok) throw new Error("Failed to fetch Excel files");
  return r.json();
}

async function fetchTestCasesRaw(source: string): Promise<Row[]> {
  const url = source ? `${API}/test-cases?source=${encodeURIComponent(source)}` : `${API}/test-cases`;
  const r = await fetch(url);
  if (!r.ok) throw new Error("Failed to fetch test cases");
  return r.json();
}

async function saveTestCasesRaw(rows: Row[], source: string): Promise<void> {
  const r = await fetch(`${API}/test-cases`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows, source }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${r.status}`);
  }
}

// ── Component ────────────────────────────────────────────────────────────────

function TestCasesPage() {
  const qc = useQueryClient();

  // ── Excel file selection ────────────────────────────────────────────────
  const { data: excelFiles = [], isLoading: filesLoading } = useQuery({
    queryKey: ["excel-files"],
    queryFn: fetchExcelFiles,
  });

  const [selectedFile, setSelectedFile] = useState<string>("");
  useEffect(() => {
    if (excelFiles.length > 0 && !selectedFile) {
      setSelectedFile(excelFiles[0]!.filename);
    }
  }, [excelFiles, selectedFile]);

  // ── Test case data ──────────────────────────────────────────────────────
  const { data: rawRows = [], isLoading: rowsLoading, refetch } = useQuery({
    queryKey: ["test-cases", selectedFile],
    queryFn: () => fetchTestCasesRaw(selectedFile),
    enabled: !!selectedFile,
  });

  // Local editable rows state (mirrors server)
  const [rows, setRows] = useState<Row[]>([]);
  // Track which row indices have been dirtied
  const [dirty, setDirty] = useState<Set<number>>(new Set());

  useEffect(() => {
    setRows(rawRows);
    setDirty(new Set());
  }, [rawRows]);

  // Derive column names from the actual Excel data
  const columns: string[] = (() => {
    if (rows.length === 0) return [];
    const keys = new Set<string>();
    rows.forEach((r) => Object.keys(r).forEach((k) => keys.add(k)));
    return Array.from(keys);
  })();

  // ── Cell editing ────────────────────────────────────────────────────────
  const updateCell = (rowIdx: number, col: string, val: string) => {
    setRows((prev) =>
      prev.map((r, i) => (i === rowIdx ? { ...r, [col]: val || null } : r))
    );
    setDirty((d) => new Set(d).add(rowIdx));
  };

  // ── Save (only dirty rows) ──────────────────────────────────────────────
  const handleSave = async () => {
    if (dirty.size === 0) { toast.info("No changes to save"); return; }
    const dirtyRows = rows.filter((_, i) => dirty.has(i));
    try {
      await saveTestCasesRaw(dirtyRows, selectedFile);
      toast.success(`Saved ${dirty.size} row(s) to ${selectedFile}`);
      setDirty(new Set());
      refetch();
    } catch (e: unknown) {
      toast.error("Save failed: " + (e instanceof Error ? e.message : String(e)));
    }
  };

  // ── Add Row ─────────────────────────────────────────────────────────────
  const addRow = () => {
    const blank: Row = {};
    columns.forEach((c) => { blank[c] = null; });
    const newIdx = rows.length;
    setRows((r) => [...r, blank]);
    setDirty((d) => new Set(d).add(newIdx));
  };

  // ── Add Column ──────────────────────────────────────────────────────────
  const [newColName, setNewColName] = useState("");
  const addColumn = () => {
    const col = newColName.trim();
    if (!col) { toast.error("Column name cannot be empty"); return; }
    if (columns.includes(col)) { toast.error("Column already exists"); return; }
    setRows((prev) => prev.map((r) => ({ ...r, [col]: null })));
    setDirty(new Set(rows.map((_, i) => i)));
    setNewColName("");
    toast.success(`Column "${col}" added — save to persist`);
  };

  // ── Delete Row ──────────────────────────────────────────────────────────
  const deleteRow = (i: number) => {
    setRows((r) => r.filter((_, idx) => idx !== i));
    toast.success("Row removed — click Save to persist");
    // Mark all remaining rows dirty so they are re-synced
    setDirty(new Set(rows.map((_, idx) => idx)));
  };

  // ── Upload Excel ─────────────────────────────────────────────────────────
  const fileInputRef = useRef<HTMLInputElement>(null);
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch(`${API}/test-cases/upload`, { method: "POST", body: fd });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${r.status}`);
      }
      const data = await r.json();
      toast.success(`Uploaded ${data.filename}`);
      qc.invalidateQueries({ queryKey: ["excel-files"] });
      setSelectedFile(data.filename);
    } catch (e: unknown) {
      toast.error("Upload failed: " + (e instanceof Error ? e.message : String(e)));
    }
    // Reset file input
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const loading = filesLoading || rowsLoading;
  const cell = "h-8 w-full min-w-[8rem] rounded-md border-border/60 bg-background/60 text-xs px-2";

  return (
    <div className="space-y-5 p-6 lg:p-8">
      {/* Header row */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Test Cases</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {rows.length} rows · {dirty.size > 0 && <span className="text-amber-500">{dirty.size} unsaved changes</span>}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Excel file selector */}
          <Select value={selectedFile} onValueChange={(v) => { setSelectedFile(v); setDirty(new Set()); }}>
            <SelectTrigger className="w-56">
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

          {/* Upload new Excel */}
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx"
            className="hidden"
            onChange={handleUpload}
          />
          <Button variant="outline" onClick={() => fileInputRef.current?.click()}>
            <Upload className="size-4" /> Upload Excel
          </Button>

          {/* Refresh */}
          <Button variant="ghost" size="icon" onClick={() => refetch()} title="Refresh from file">
            <RefreshCw className="size-4" />
          </Button>

          {/* Save */}
          <Button onClick={handleSave} disabled={dirty.size === 0}>
            <Save className="size-4" /> Save Changes
          </Button>
        </div>
      </div>

      {/* Add column row */}
      <div className="flex items-center gap-2">
        <Input
          className="h-8 w-56 text-xs"
          placeholder="New column name…"
          value={newColName}
          onChange={(e) => setNewColName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addColumn()}
        />
        <Button variant="outline" size="sm" onClick={addColumn} disabled={!newColName.trim()}>
          <Plus className="size-3.5" /> Add Column
        </Button>
      </div>

      {/* Table */}
      <Card className="border-border/70 p-0">
        {loading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-10 rounded-md" />
            ))}
          </div>
        ) : columns.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">
            No data found. Upload an Excel file or select an existing one.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr className="border-b border-border text-[11px] uppercase tracking-wider text-muted-foreground">
                  {columns.map((col) => (
                    <th key={col} className="p-3 font-medium whitespace-nowrap">
                      {col}
                    </th>
                  ))}
                  <th className="p-3" />
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr
                    key={i}
                    className={
                      "border-b border-border/60 align-top " +
                      (dirty.has(i) ? "bg-amber-500/5" : "")
                    }
                  >
                    {columns.map((col) => (
                      <td key={col} className="p-1.5">
                        <Input
                          className={cell}
                          value={row[col] ?? ""}
                          onChange={(e) => updateCell(i, col, e.target.value)}
                        />
                      </td>
                    ))}
                    <td className="p-1.5">
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Delete row"
                        onClick={() => deleteRow(i)}
                      >
                        <Trash2 className="size-4 text-destructive" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Add Row */}
        <div className="p-4">
          <Button variant="outline" onClick={addRow}>
            <Plus className="size-4" /> Add Row
          </Button>
        </div>
      </Card>
    </div>
  );
}