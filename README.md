# BI Dashboard Automation Framework (Pytest + Playwright)

This project is a sample automation framework for web / BI dashboards,
implemented using:

- Python
- Playwright (sync)
- Pytest
- Page Object Model (POM)
- Excel-driven test data
- HTML reporting with screenshots and log snippets
- Optional encryption utility for sensitive passwords

## Structure

- `config/` – environment config (URLs, timeouts, browser settings)
- `data/` – test data access (Excel wrapper)
- `locators/` – selectors for each page
- `pageobjects/` – POM classes wrapping Playwright `Page`
- `methods/` – reusable business flows (login, common actions)
- `utils/` – logger, waits, Excel helper, encryption, screenshots
- `tests/` – pytest tests + fixtures
- `reports/html_reports/` – HTML report + CSS
- `testdata/` – Excel files with test cases

## Usage

```bash
pip install -r requirements.txt
playwright install chromium

pytest -m login
```

The HTML report is generated at:

`reports/html_reports/test_report.html`