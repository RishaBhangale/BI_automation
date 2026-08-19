# BI Validator Hub

Build a modern dark-mode test automation control panel called "BI Validator". 

TECH: React, TypeScript, Tailwind CSS, shadcn/ui components. Clean sidebar layout.

SIDEBAR NAVIGATION (left, 240px wide, dark indigo):

- Dashboard (home icon) — default view

- Run Tests (play icon)

- Test History (clock icon)

- Test Cases (table icon)  

- Configs (settings icon)

- Export (download icon)

PAGE 1 — DASHBOARD (home):

Top stats bar with 4 metric cards: Total Runs, Passed, Failed, Avg Duration.

Below: a bar chart (last 7 runs pass/fail count). Below that: last 3 run cards in a grid — each showing timestamp, total/passed/failed count, duration, and a green/red badge.

PAGE 2 — RUN TESTS:

Left panel (40% width): 

  - Dropdown "Dashboard Config" (lists YAML file names)

  - Checklist of test cases (Test ID + Scenario Name) from the Excel file, with Select All toggle

  - "Run Selected Tests" primary button (indigo, full width)

Right panel (60% width, dark bg):

  - Live log stream area — monospaced font, scrolls automatically, color-coded lines:

    PASS = green, FAIL = red, INFO = gray, STEP = white bold, ERROR = orange

  - Progress bar at top showing X/total tests completed

  - "Stop Run" button when active

PAGE 3 — TEST HISTORY:

Table with columns: Run ID, Date/Time, Config Used, Total, Passed, Failed, Duration, Actions.

Actions column: "View Report" (eye icon, opens modal) and "Export PDF" (download icon).

Clicking View Report opens a modal with:

  - Summary header (same 4 metric cards style)

  - Test result cards grouped by status

  - Each card: TC ID, Scenario Name, status badge, duration, expandable "Steps" section that shows step-by-step log entries with timestamps

PAGE 4 — TEST CASES:

Editable data table showing all rows from business_scenarios.xlsx:

  Columns: Test ID, Scenario Name, Slicer 1 Name, Slicer 1 Value, ...(up to Slicer 6), KPI to Read, SQL File Name

  Each cell is editable inline. "Add Row" button at bottom. "Save to Excel" button in top right. "Delete Row" button per row (trash icon).

PAGE 5 — CONFIGS:

Card grid of all YAML config files. Each card shows: file name, dashboard name, number of pages, DB driver.

Clicking opens a code editor view (Monaco or CodeMirror) to edit the YAML inline. Save button.

PAGE 6 — EXPORT:

Select a past run from dropdown. Choose format: PDF or HTML. 

Preview pane shows a thumbnail/summary of the report.

"Export & Download" button.

GLOBAL:

- Header bar: "BI Validator" logo left, current user right, dark mode toggle

- Toast notifications for all async actions (run started, export complete, save success)

- Loading skeletons on all data loads

- Responsive design but optimized for desktop (1280px+)

- Color palette: indigo-900 primary, dark gray backgrounds, white text

This project was built with [Lovable](https://lovable.dev).

## Build with Lovable

Continue developing this project in the [Lovable editor](https://lovable.dev/projects/8fd704b8-1a18-484b-a3d7-013cee96e454).

- **Ship faster**: describe what you want to build and Lovable handles the code.
- **Stay in sync**: every change made in Lovable is committed straight to this repository.
- **Full ownership**: this code is yours. Push to `main` on GitHub and your changes sync back into Lovable, ready for your next prompt.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```
