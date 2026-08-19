import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

export function AppHeader() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-border bg-card/50 px-6 backdrop-blur">
      <div className="flex items-center gap-3">
        <span className="text-base font-semibold tracking-tight md:hidden">Automated BI Testing</span>
        <span className="hidden text-sm text-muted-foreground md:inline">
          Automated BI Testing — Dashboard Regression &amp; KPI Validation
        </span>
      </div>
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" aria-label="Toggle dark mode" onClick={() => setDark((d) => !d)}>
          {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </Button>
        <div className="flex items-center gap-2">
          <div className="hidden text-right leading-tight sm:block">
            <p className="text-sm font-medium">Rishabh Bhangale</p>
          </div>
          <Avatar className="size-8">
            <AvatarFallback className="bg-primary text-primary-foreground text-xs">RB</AvatarFallback>
          </Avatar>
        </div>
      </div>
    </header>
  );
}