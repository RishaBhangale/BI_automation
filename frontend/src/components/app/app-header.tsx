import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

export function AppHeader() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  return (
    <header
      className="relative z-20 flex h-16 w-full shrink-0 items-center overflow-hidden"
      style={{
        background: "var(--sidebar)",
        borderBottom: "1px solid rgba(255,255,255,0.12)",
        boxShadow: "0 2px 12px rgba(0,0,0,0.25)",
      }}
    >
      {/* Decorative soft circles */}
      <div
        className="pointer-events-none absolute"
        style={{
          right: "-60px", top: "-80px",
          width: 320, height: 320,
          borderRadius: "50%",
          background: "rgba(255,255,255,0.04)",
        }}
      />
      <div
        className="pointer-events-none absolute"
        style={{
          right: 140, bottom: "-60px",
          width: 180, height: 180,
          borderRadius: "50%",
          background: "rgba(255,255,255,0.03)",
        }}
      />

      {/*
        Left section — EXACTLY w-56 (14rem / 224px) to match the sidebar width.
        The divider on its right edge will align perfectly with the sidebar border.
      */}
      <div className="relative flex w-56 shrink-0 items-center justify-center px-4">
        <img
          src="https://www.c5i.ai/wp-content/themes/course5iTheme/new-assets/images/c5i-primary-logo.svg"
          alt="C5i"
          className="h-11 w-auto"
          style={{ filter: "brightness(0) invert(1)" }}
        />
      </div>

      {/* Divider — sits exactly at the sidebar edge */}
      <div
        className="relative h-9 shrink-0"
        style={{ width: "2px", background: "rgba(255,255,255,0.45)", borderRadius: "1px" }}
      />

      {/* Right section — title + dark mode toggle */}
      <div className="relative flex min-w-0 flex-1 items-center justify-between px-6">
        <p className="truncate text-sm font-medium" style={{ color: "rgba(255,255,255,0.85)" }}>
          Automated BI Testing — Dashboard Regression &amp; KPI Validation
        </p>

        <button
          type="button"
          onClick={() => setDark((d) => !d)}
          aria-label="Toggle dark mode"
          className="ml-4 flex size-8 shrink-0 items-center justify-center rounded-md transition-colors"
          style={{
            background: "rgba(255,255,255,0.1)",
            border: "1px solid rgba(255,255,255,0.2)",
          }}
        >
          {dark
            ? <Sun className="size-4 text-white" />
            : <Moon className="size-4 text-white" />}
        </button>
      </div>
    </header>
  );
}
