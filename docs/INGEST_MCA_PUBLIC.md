# MCA Public Portal scraper — operator runbook

> PRD §10 free-source plan, Phase A. Free replacement for both the paid
> MCA21 V3 REST API and the paid CERSAI per-query portal. Scrapes
> mca.gov.in's public Master Data + Signatory Details + Index of
> Charges pages via Playwright.

## Why this exists

`MCA21_API_KEY` is a paid subscription; `CERSAI_API_KEY` is paid per
query. Neither is available on the hackathon budget. The MCA public
portal at mca.gov.in is verified free (no login required for basic
lookups; captcha-gated on first session). This scraper replaces both
in one Playwright-driven flow.

## How it fits

`CompositeCompanySource` fall-through:

```
1. MCA21V3Source        — paid, only when MCA21_API_KEY is real
2. MCAPublicScraper     — free Playwright scrape (THIS)
3. FixtureSource        — demo-backbone JSON in infra/seeds/companies/
```

When `MCAPublicScraper` has no fetcher injected, it raises
`MCAPublicFetcherNotConfiguredError` and the composite cleanly falls
through to FixtureSource — same convention as `MCA21KeyMissingError`.

For production runs, inject `MCAPublicPlaywrightFetcher`:

```python
from backend.app.ingest.composite import CompositeCompanySource
from backend.app.ingest.mca_public import MCAPublicScraper
from backend.app.ingest.mca_public_playwright import MCAPublicPlaywrightFetcher

scraper = MCAPublicScraper(fetcher=MCAPublicPlaywrightFetcher())
composite = CompositeCompanySource(secondary=scraper)
```

## One-time captcha bootstrap

The MCA portal serves a captcha on the first lookup of a fresh session.
Solve it once in a visible browser; the scraper caches the resulting
session cookie for ~15 minutes of headless follow-up scraping.

```bash
# from the repo root, with the .venv activated
python -m backend.app.ingest.mca_public_playwright --bootstrap
```

This opens a Chromium window at the MCA Master Data search page.

1. Search any CIN you have access to (e.g. `U45201MH2005PTC155294`).
2. Solve the captcha when prompted.
3. Wait until the master-data result page renders.
4. Switch back to the terminal and press **Enter**.

The script captures the session cookies, writes them to
`MCA_PUBLIC_SESSION_DIR/cookies.json` (default `./.mca_session/`), and
exits. From that point until expiry, headless calls reuse the cookie
jar.

## When the session expires

After ~15 minutes of idle (or after MCA's server-side timeout), the
next headless scrape returns `None` and logs:

```
MCA Playwright session stale or missing — falling through.
Operator: run `python -m backend.app.ingest.mca_public_playwright --bootstrap` to refresh.
```

The composite cleanly falls through to FixtureSource. Demo CINs still
work. To re-enable live scraping, re-run `--bootstrap`.

## What's available, what's NOT

| Field on `CompanyBundle` | Source page | Free? |
|---|---|---|
| `RawCompany.*` | View Company/LLP Master Data | ✅ |
| `RawDirector.*` | View Signatory Details | ✅ |
| `RawCharge.*` | View Index of Charges | ✅ (replaces CERSAI) |
| `RawFinancialStatement.*` | "View Public Documents" → AOC-4 PDF | ❌ Per-document download fee |

AOC-4 financial statements are NOT free per-CIN. The Day-16
`/upload/financials` endpoint remains the substitute: an analyst
uploads the PDF they have access to, and `pdfplumber` extracts the
rows. Modules M1 (Beneish), M2 (cross-statement), M5 (peer-deviation),
M6 (temporal), M7 (auditor-NLP) all consume that upload overlay.

## What breaks first when MCA changes their HTML

Layout drift on mca.gov.in is the most likely failure mode. The
production-fetcher selectors are pinned at the top of each scrape
branch in
[`backend/app/ingest/mca_public_playwright.py`](../backend/app/ingest/mca_public_playwright.py):

| Branch | Selector to update |
|---|---|
| `_scrape_master` | `input[name="companyID"]`, `table.master-data-table tr` |
| `_scrape_signatories` | `"text=Signatory Details"`, `table.signatory-details-table tbody tr` |
| `_scrape_charges` | `"text=Index of Charges"`, `table.index-of-charges-table tbody tr` |

If a scrape suddenly returns `None` or partial data after MCA pushes a
redesign:

1. Run `--bootstrap` with the headed browser visible.
2. Inspect the current selectors via DevTools.
3. Update the four selector strings.
4. The orchestrator (`mca_public.py`) is selector-free — it only sees
   the dict the fetcher emits — so test stays green if you preserve
   the same key names.

## Why not 2captcha / anti-captcha

Captcha-solving SaaS is technically feasible but introduces a paid
dependency that contradicts the entire point of this phase. Stay on
the manual-bootstrap path. If the operator burden becomes painful,
the right next step is monitoring the cookie expiry + sending a
reminder, not paying a captcha vendor.

## CI safety

Tests for `MCAPublicScraper` in
[`backend/tests/test_mca_public_scraper.py`](../backend/tests/test_mca_public_scraper.py)
inject a canned async fetcher — they NEVER load Playwright. So:

- `python -m pytest` passes without the `playwright` Python package
  installed on the CI runner.
- The `backend/app/ingest/mca_public_playwright.py` module imports
  Playwright lazily (inside method bodies, not at module top), so
  even `python -c "import backend.app.ingest.mca_public_playwright"`
  works without the package.

You only need `pip install playwright && playwright install chromium`
on the machine that actually runs live scrapes (typically your
workstation or the Oracle VM, never CI).

## Files in this slice

- [`backend/app/ingest/mca_public.py`](../backend/app/ingest/mca_public.py) — orchestrator + parsers.
- [`backend/app/ingest/mca_public_playwright.py`](../backend/app/ingest/mca_public_playwright.py) — production Playwright fetcher.
- [`backend/app/ingest/composite.py`](../backend/app/ingest/composite.py) — wires this in as the 2nd-tier primary.
- [`backend/tests/test_mca_public_scraper.py`](../backend/tests/test_mca_public_scraper.py) — 9 unit cases.
- This runbook.
