# RBI Wilful Defaulter scraper — operator runbook

> PRD §10 Phase B free-source plan. Replaces the `RBIWilfulScraper.fetch_all()`
> stub with a real scrape of rbi.org.in's free defaulter web pages.

## Why this exists

The RBI publishes the wilful-defaulter / suit-filed lists in two forms:

| Form | Pricing | Used here |
|---|---|---|
| Booklet / CD compilation (quarterly) | Paid | ❌ |
| Web pages at rbi.org.in | **Free, no login** | ✅ |

We only ever hit the web pages.

## How it fits

`backend/app/ingest/wilful_defaulter.py` ships two sources that satisfy
the same `WilfulDefaulterSource` protocol:

```
RBIWilfulScraper            — free web scrape (THIS)
WilfulDefaulterFixtureSource — infra/seeds/wilful_defaulter/*.json (demo backbone)
```

Module 9 (`m09_nclt_defaulter.py`) consumes whichever source the operator
wires in. The fixture stays canonical for demo CINs; the scraper output
is written to `data/processed/wilful_defaulter_<quarter>.json` so layout
drift on rbi.org.in cannot silently overwrite the IL&FS / DHFL rows that
the storyboard depends on.

## What's scraped

Two pages, combined and deduplicated by `(cin, din, bank_name)`:

| Page | Default URL | Source label on emitted rows |
|---|---|---|
| Wilful defaulters (non-suit-filed) | `RBI_DEFAULT_LIST_URL` | `RBI` |
| Suit-filed defaulters | `RBI_DEFAULT_SUIT_FILED_URL` | `SUIT_FILED` |

CIBIL's public defaulter list has the same column shape — point the same
scraper at the CIBIL URLs via the constructor when needed:

```python
from backend.app.ingest.wilful_defaulter import RBIWilfulScraper

scraper = RBIWilfulScraper(
    fetch_html=my_httpx_fetcher,
    list_url="https://www.cibil.com/.../wilful-defaulters",
    suit_filed_url="https://www.cibil.com/.../suit-filed",
)
```

## Production wiring

```python
import httpx
from backend.app.ingest.wilful_defaulter import RBIWilfulScraper

async def fetch_html(url: str) -> str:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "sentinel-g/1.0"})
        r.raise_for_status()
        return r.text

scraper = RBIWilfulScraper(fetch_html=fetch_html)
records = await scraper.fetch_all()
```

When `fetch_html` is not injected, `fetch_all()` raises
`NotImplementedError` — the operator path falls back to the fixture.

## Quarterly refresh cadence

RBI refreshes the lists at the end of every quarter. The scheduler
(`backend/app/ingest/scheduler.py`) already polls `scheduler_wilful_poll_sec`
every 30 days, which is conservative enough to catch every release.

Manual refresh (operator-side):

```bash
# from the repo root, with the .venv activated
python -m backend.app.ingest.wilful_defaulter --refresh
```

(Provide this CLI entry point separately if running ad-hoc — the file
currently exposes the scraper class only; wire `python -m` later if
operator demand justifies it.)

## What breaks first when RBI changes their HTML

The parser is intentionally tolerant — column-order drift does not break
it, because `parse_rbi_wilful_html` locates the CIN cell via the CIN regex
rather than positional indexing. The header heuristics fall back gracefully:

| Field | Header heuristic (case-insensitive substring) |
|---|---|
| `bank_name` | `bank`, `lender` |
| `amount` | `amount`, `outstanding` |
| `declared_date` | `date`, `declared` |
| `cin` | (regex match in any cell) |
| `din` | (regex match in any cell) |

If a column name changes to something exotic (e.g. "Sanctioning
Authority" in place of "Bank Name"), update the heuristic list in
[`backend/app/ingest/rbi_html_parser.py`](../backend/app/ingest/rbi_html_parser.py).
Tests at [`backend/tests/test_rbi_wilful_scraper.py`](../backend/tests/test_rbi_wilful_scraper.py)
include a column-reshuffle case that pins the resilience.

## What's NOT in scope

| Out of scope | Why |
|---|---|
| Captcha-solving / login flows | rbi.org.in serves the lists without authentication. If RBI ever puts the lists behind a captcha, switch the fetcher to a Playwright bootstrap mirror of `mca_public_playwright.py`. |
| PDF / Excel parsing | RBI's *web* pages render HTML tables. The booklet PDFs are the priced compilation; we don't touch them. |
| CIBIL paid API | CIBIL's free public defaulter list is HTML and shares the same column shape; point this same scraper at the CIBIL URL pair when needed. The paid CIBIL CIR API is not used. |

## CI safety

Tests at
[`backend/tests/test_rbi_wilful_scraper.py`](../backend/tests/test_rbi_wilful_scraper.py)
inject a canned-HTML coroutine — they NEVER hit rbi.org.in. So:

- `python -m pytest` passes without network access on CI.
- The module imports zero third-party HTTP libraries at import time
  (`httpx` is only mentioned in the production-wiring snippet above, not
  in the scraper itself).

## Files in this slice

- [`backend/app/ingest/wilful_defaulter.py`](../backend/app/ingest/wilful_defaulter.py) — `RawWilfulDefaulter`, fixture source, real scraper.
- [`backend/app/ingest/rbi_html_parser.py`](../backend/app/ingest/rbi_html_parser.py) — stdlib HTML table parser.
- [`backend/tests/test_rbi_wilful_scraper.py`](../backend/tests/test_rbi_wilful_scraper.py) — 9 unit cases.
- This runbook.
