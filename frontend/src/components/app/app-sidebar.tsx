import { Link, useRouterState } from "@tanstack/react-router";
import { Home, Play, Clock, Table2, Settings, Download, Search, ShieldCheck } from "lucide-react";

const items = [
  { title: "Dashboard", url: "/", icon: Home },
  { title: "Discovery", url: "/discovery", icon: Search },
  { title: "Run Tests", url: "/run", icon: Play },
  { title: "Test History", url: "/history", icon: Clock },
  { title: "Test Cases", url: "/test-cases", icon: Table2 },
  { title: "Configs", url: "/configs", icon: Settings },
  { title: "Export", url: "/export", icon: Download },
] as const;

export function AppSidebar() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar md:flex">
      <div className="flex h-16 items-center gap-2 border-b border-sidebar-border px-5">
        <span className="flex size-8 items-center justify-center rounded-md bg-sidebar-primary">
          <ShieldCheck className="size-4 text-sidebar-primary-foreground" />
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
          const active = pathname === item.url;
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
      </nav>
      <div className="border-t border-sidebar-border p-4 text-[11px] text-sidebar-foreground/50">
        v2.4.1 · 4 configs loaded
      </div>
    </aside>
  );
}