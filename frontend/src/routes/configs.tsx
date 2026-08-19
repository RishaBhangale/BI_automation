import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { ArrowLeft, Database, FileCode2, Layers, Save } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchConfigs, saveConfig } from "@/lib/api-client";
import { useQuery, useQueryClient } from "@tanstack/react-query";

export const Route = createFileRoute("/configs")({
  head: () => ({
    meta: [
      { title: "Configs — Automated BI Testing - Validation" },
      {
        name: "description",
        content: "Manage YAML dashboard configs: pages, slicers, database drivers and tolerances.",
      },
      { property: "og:title", content: "Configs — Automated BI Testing - Validation" },
      { property: "og:description", content: "Manage YAML dashboard configs and database drivers." },
    ],
  }),
  component: Configs,
});

function Configs() {
  const qc = useQueryClient();
  const { data, isLoading: loading } = useQuery({ queryKey: ["configs"], queryFn: fetchConfigs });
  const configFiles = data ?? [];

  const [editing, setEditing] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  useEffect(() => {
    if (data) {
      setDrafts((prev) => {
        const next = { ...prev };
        data.forEach((c) => {
          if (next[c.file] === undefined) next[c.file] = c.content;
        });
        return next;
      });
    }
  }, [data]);

  const active = configFiles.find((c) => c.file === editing);

  if (active) {
    const value = drafts[active.file] ?? "";
    const lines = value.split("\n");

    const handleSave = () => {
      saveConfig(active.file, value)
        .then(() => {
          toast.success(active.file + ' saved');
          qc.invalidateQueries({ queryKey: ['configs'] });
        })
        .catch(e => toast.error('Save failed: ' + e.message));
    };

    return (
      <div className="space-y-4 p-6 lg:p-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="icon" onClick={() => setEditing(null)} aria-label="Back">
              <ArrowLeft className="size-4" />
            </Button>
            <div>
              <h1 className="font-mono text-lg font-semibold">{active.file}</h1>
              <p className="text-sm text-muted-foreground">{active.dashboard}</p>
            </div>
          </div>
          <Button onClick={handleSave}>
            <Save className="size-4" /> Save
          </Button>
        </div>

        <Card className="overflow-hidden border-border/70 bg-panel p-0">
          <div className="flex items-center gap-2 border-b border-border/70 px-4 py-2 text-xs text-muted-foreground">
            <FileCode2 className="size-3.5" /> YAML editor · {lines.length} lines
          </div>
          <div className="flex">
            <div className="select-none border-r border-border/70 bg-background/50 px-3 py-4 text-right font-mono text-xs text-muted-foreground">
              {lines.map((_, i) => (
                <div key={i}>{i + 1}</div>
              ))}
            </div>
            <textarea
              spellCheck={false}
              value={value}
              onChange={(e) => setDrafts((d) => ({ ...d, [active.file]: e.target.value }))}
              className="min-h-[520px] w-full resize-none bg-transparent p-4 font-mono text-xs leading-[1.5] text-foreground outline-none"
            />
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6 lg:p-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Configs</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          YAML definitions for each dashboard under validation.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {loading
          ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-40 rounded-xl" />)
          : configFiles.map((c) => (
              <Card
                key={c.file}
                onClick={() => setEditing(c.file)}
                className="metric-card cursor-pointer gap-0 border-border/70 p-5 transition-colors hover:border-primary/60"
              >
                <div className="flex items-center gap-2">
                  <FileCode2 className="size-4 text-primary" />
                  <span className="font-mono text-sm">{c.file}</span>
                </div>
                <p className="mt-3 text-lg font-semibold">{c.dashboard}</p>
                <div className="mt-4 flex flex-wrap gap-4 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1">
                    <Layers className="size-3" /> {c.pages} pages
                  </span>
                  <span className="flex items-center gap-1">
                    <Database className="size-3" /> {c.driver}
                  </span>
                </div>
              </Card>
            ))}
      </div>
    </div>
  );
}