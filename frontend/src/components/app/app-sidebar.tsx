import { Link, useRouterState } from "@tanstack/react-router";
import { Home, Play, Clock, Table2, Circle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { fetchConfigs } from "@/lib/api-client";
import { useRunContext } from "@/context/run-context";

const items = [
  { title: "Dashboard", url: "/", icon: Home },
  { title: "Run Tests", url: "/run", icon: Play },
  { title: "Test History", url: "/history", icon: Clock },
  { title: "Test Data", url: "/test-cases", icon: Table2 },
] as const;

export function AppSidebar() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { data: configsData } = useQuery({ queryKey: ["configs"], queryFn: fetchConfigs });
  const configCount = configsData?.length ?? 0;

  const { activeRun, activeDiscovery } = useRunContext();

  const runPillText = activeRun?.running
    ? `● ${activeRun.runId} · ${activeRun.done}/${activeRun.total}`
    : null;
  const discPillText = activeDiscovery?.running
    ? `● Discovery running…`
    : null;

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar md:flex">
      <div className="flex h-16 items-center gap-2 border-b border-sidebar-border px-5">
        <span className="flex size-8 items-center justify-center rounded-md bg-sidebar-primary">
          <Play className="size-4 text-sidebar-primary-foreground" />
        </span>
        <div className="leading-tight">
          <p className="text-sm font-semibold text-sidebar-foreground">Automated BI Testing</p>
          <p className="text-[10px] uppercase tracking-widest text-sidebar-foreground/50">
            Validation Suite
          </p>
        </div>
      </div>
      <nav className="flex flex-1 flex-col gap-1 p-3">
        {items.map((item) => {
          const active = item.url === "/" ? pathname === "/" : pathname.startsWith(item.url);
          return (
            <Link
              key={item.url}
              to={item.url}
              className={
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors " +
                (active
                  ? "bg-sidebar-primary text-sidebar-primary-foreground font-medium"
                  : "text-sidebar-foreground/75 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground")
              }
            >
              <item.icon className="size-4" />
              {item.title}
            </Link>
          );
        })}

        {/* Active run / discovery status pill */}
        {(runPillText || discPillText) && (
          <div className="mt-3 rounded-md border border-primary/30 bg-primary/5 px-3 py-2">
            {runPillText && (
              <p className="flex items-center gap-1.5 text-[11px] font-medium text-primary truncate">
                <Circle className="size-2 fill-green-400 text-green-400 animate-pulse shrink-0" />
                {runPillText}
              </p>
            )}
            {discPillText && (
              <p className="flex items-center gap-1.5 text-[11px] font-medium text-blue-500 truncate">
                <Circle className="size-2 fill-blue-400 text-blue-400 animate-pulse shrink-0" />
                {discPillText}
              </p>
            )}
          </div>
        )}
      </nav>
      <div className="border-t border-sidebar-border p-4 text-[11px] text-sidebar-foreground/50">
        v2.5.0 · {configCount} config{configCount !== 1 ? "s" : ""} loaded
      </div>
    </aside>
  );
}
