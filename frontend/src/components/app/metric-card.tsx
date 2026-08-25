import type { LucideIcon } from "lucide-react";
import { Card } from "@/components/ui/card";

export function MetricCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "default",
}: {
  label: string;
  value: string | number;
  hint?: string;
  icon?: LucideIcon;
  tone?: "default" | "success" | "danger";
}) {
  const toneClass =
    tone === "success" ? "text-success" : tone === "danger" ? "text-destructive" : "text-foreground";
  return (
    <Card className="metric-card gap-0 border-border/70 p-4">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{label}</p>
        {Icon ? <Icon className="size-3.5 text-muted-foreground" /> : null}
      </div>
      <p className={"mt-2 text-2xl font-semibold tabular-nums " + toneClass}>{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p> : null}
    </Card>
  );
}

export function StatusBadge({ failed }: { failed: number }) {
  return failed === 0 ? (
    <span className="rounded-full bg-success/15 px-2.5 py-1 text-xs font-medium text-success">
      Passed
    </span>
  ) : (
    <span className="rounded-full bg-destructive/15 px-2.5 py-1 text-xs font-medium text-destructive">
      {failed} Failed
    </span>
  );
}