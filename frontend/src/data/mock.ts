export type Status = "PASS" | "FAIL";

export type Step = { time: string; level: LogLevel; message: string };

export type LogLevel = "PASS" | "FAIL" | "INFO" | "STEP" | "ERROR";

export type TestResult = {
  id: string;
  scenario: string;
  status: Status;
  duration: string;
  steps: Step[];
};

export type Run = {
  runId: string;
  timestamp: string;
  config: string;
  total: number;
  passed: number;
  failed: number;
  duration: string;
  results: TestResult[];
};

export type TestCase = {
  testId: string;
  scenario: string;
  slicers: { name: string; value: string }[];
  kpi: string;
  sqlFile: string;
};

export const configFiles = [
  {
    file: "sales_dashboard.yaml",
    dashboard: "Sales Performance",
    pages: 6,
    driver: "Snowflake ODBC",
    content: `dashboard:
  name: Sales Performance
  url: https://bi.internal/app/sales
  pages:
    - name: Overview
      slicers: [Region, Quarter]
    - name: Pipeline
      slicers: [Owner, Stage]
database:
  driver: Snowflake ODBC
  warehouse: WH_ANALYTICS
  schema: MART_SALES
  timeout: 120
tolerance: 0.01
`,
  },
  {
    file: "finance_dashboard.yaml",
    dashboard: "Finance Close",
    pages: 4,
    driver: "PostgreSQL",
    content: `dashboard:
  name: Finance Close
  url: https://bi.internal/app/finance
  pages:
    - name: P&L
      slicers: [Entity, Period]
    - name: Cash Flow
      slicers: [Currency]
database:
  driver: PostgreSQL
  host: pg-analytics.internal
  schema: fin_mart
  timeout: 90
tolerance: 0.005
`,
  },
  {
    file: "supply_chain.yaml",
    dashboard: "Supply Chain Ops",
    pages: 8,
    driver: "MS SQL Server",
    content: `dashboard:
  name: Supply Chain Ops
  url: https://bi.internal/app/supply
  pages:
    - name: Inventory
      slicers: [Plant, SKU Group]
database:
  driver: MS SQL Server
  host: sql-dw.internal
  schema: dbo
  timeout: 60
tolerance: 0.02
`,
  },
  {
    file: "marketing_kpis.yaml",
    dashboard: "Marketing KPIs",
    pages: 3,
    driver: "BigQuery",
    content: `dashboard:
  name: Marketing KPIs
  url: https://bi.internal/app/marketing
  pages:
    - name: Campaigns
      slicers: [Channel, Month]
database:
  driver: BigQuery
  project: acme-analytics
  dataset: mkt_mart
tolerance: 0.01
`,
  },
];

const slicer = (name: string, value: string) => ({ name, value });

export const testCases: TestCase[] = [
  {
    testId: "TC_001",
    scenario: "Total Revenue by Region — EMEA Q1",
    slicers: [slicer("Region", "EMEA"), slicer("Quarter", "Q1"), slicer("Currency", "USD")],
    kpi: "Total Revenue",
    sqlFile: "revenue_emea_q1.sql",
  },
  {
    testId: "TC_002",
    scenario: "Gross Margin % — APAC Q2",
    slicers: [slicer("Region", "APAC"), slicer("Quarter", "Q2")],
    kpi: "Gross Margin %",
    sqlFile: "margin_apac_q2.sql",
  },
  {
    testId: "TC_003",
    scenario: "Open Pipeline by Stage — Enterprise",
    slicers: [slicer("Segment", "Enterprise"), slicer("Stage", "Negotiation")],
    kpi: "Pipeline Value",
    sqlFile: "pipeline_enterprise.sql",
  },
  {
    testId: "TC_004",
    scenario: "Inventory Turns — Plant 1042",
    slicers: [slicer("Plant", "1042"), slicer("SKU Group", "Fasteners")],
    kpi: "Inventory Turns",
    sqlFile: "inv_turns_1042.sql",
  },
  {
    testId: "TC_005",
    scenario: "Cash Conversion Cycle — FY Close",
    slicers: [slicer("Entity", "ACME-DE"), slicer("Period", "FY2025")],
    kpi: "CCC Days",
    sqlFile: "ccc_fy2025.sql",
  },
  {
    testId: "TC_006",
    scenario: "CAC by Channel — Paid Search",
    slicers: [slicer("Channel", "Paid Search"), slicer("Month", "2026-05")],
    kpi: "CAC",
    sqlFile: "cac_paid_search.sql",
  },
  {
    testId: "TC_007",
    scenario: "Churn Rate — SMB Cohort",
    slicers: [slicer("Segment", "SMB"), slicer("Cohort", "2025-H2")],
    kpi: "Churn Rate",
    sqlFile: "churn_smb.sql",
  },
  {
    testId: "TC_008",
    scenario: "On-Time Delivery — Carrier Mix",
    slicers: [slicer("Carrier", "All"), slicer("Region", "NA")],
    kpi: "OTD %",
    sqlFile: "otd_na.sql",
  },
];

const steps = (id: string, ok: boolean): Step[] => [
  { time: "10:24:01", level: "STEP", message: `Opening dashboard page for ${id}` },
  { time: "10:24:03", level: "INFO", message: "Waiting for visuals to hydrate (2.1s)" },
  { time: "10:24:05", level: "STEP", message: "Applying slicer selections" },
  { time: "10:24:07", level: "INFO", message: "Executing SQL baseline query against warehouse" },
  ok
    ? { time: "10:24:11", level: "PASS", message: "KPI 4,182,900.00 matches baseline (delta 0.00%)" }
    : { time: "10:24:11", level: "ERROR", message: "KPI 3,910,412.00 vs baseline 4,182,900.00" },
  ok
    ? { time: "10:24:11", level: "INFO", message: "Screenshot archived" }
    : { time: "10:24:12", level: "FAIL", message: "Delta 6.51% exceeds tolerance 1.00%" },
];

function buildRun(
  runId: string,
  timestamp: string,
  config: string,
  failIds: string[],
  duration: string,
): Run {
  const results = testCases.map((tc) => {
    const failed = failIds.includes(tc.testId);
    return {
      id: tc.testId,
      scenario: tc.scenario,
      status: (failed ? "FAIL" : "PASS") as Status,
      duration: `${(8 + (tc.testId.charCodeAt(5) % 7) + (failed ? 4 : 0)).toFixed(1)}s`,
      steps: steps(tc.testId, !failed),
    };
  });
  const failedCount = results.filter((r) => r.status === "FAIL").length;
  return {
    runId,
    timestamp,
    config,
    total: results.length,
    passed: results.length - failedCount,
    failed: failedCount,
    duration,
    results,
  };
}

export const runs: Run[] = [
  buildRun("RUN-2041", "2026-08-18 09:12", "sales_dashboard.yaml", ["TC_004"], "1m 48s"),
  buildRun("RUN-2040", "2026-08-17 18:40", "finance_dashboard.yaml", ["TC_002", "TC_007"], "2m 04s"),
  buildRun("RUN-2039", "2026-08-17 11:05", "supply_chain.yaml", [], "1m 39s"),
  buildRun("RUN-2038", "2026-08-16 16:22", "marketing_kpis.yaml", ["TC_006"], "1m 51s"),
  buildRun("RUN-2037", "2026-08-15 10:02", "sales_dashboard.yaml", ["TC_001", "TC_003", "TC_008"], "2m 22s"),
  buildRun("RUN-2036", "2026-08-14 09:47", "finance_dashboard.yaml", [], "1m 33s"),
  buildRun("RUN-2035", "2026-08-13 14:18", "supply_chain.yaml", ["TC_005"], "1m 57s"),
];

export const stats = {
  totalRuns: 128,
  passed: runs.reduce((a, r) => a + r.passed, 0),
  failed: runs.reduce((a, r) => a + r.failed, 0),
  avgDuration: "1m 54s",
};

export const chartData = [...runs]
  .reverse()
  .map((r) => ({ run: r.runId.replace("RUN-", "#"), passed: r.passed, failed: r.failed }));