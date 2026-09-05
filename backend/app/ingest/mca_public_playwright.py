"""Production Playwright fetcher for the MCA public portal.

PRD §10 free-source plan, Phase A. Concrete implementation of the
`MCAPublicFetcherProtocol` from `mca_public.py`. Owns the browser
lifecycle and the captcha-bootstrap dance so the orchestrator stays
test-friendly (no browser dependency in CI).

Operator flow (one-time bootstrap):

    python -m backend.app.ingest.mca_public_playwright --bootstrap

Opens a visible Chromium window pointed at the MCA Master Data search
page. Operator solves the captcha + does one real lookup. Once the
landing page is reached, Playwright dumps the session cookies to
`MCA_PUBLIC_SESSION_DIR/cookies.json`. Subsequent headless fetches reuse
that cookie jar until it expires (~15 min on mca.gov.in's session).

When the cookie expires the fetcher logs a warning and the composite
falls through to FixtureSource until the operator re-bootstraps.
Captcha-solving SaaS integration (2captcha etc.) is intentionally
out-of-scope for this iteration.

Playwright is imported lazily — `from playwright.async_api import ...`
only fires inside `__aenter__` / `bootstrap()`. The orchestrator at
`mca_public.MCAPublicScraper` can still be imported + unit-tested with
this module on the path, even when the `playwright` Python package
isn't installed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from backend.app.config import get_settings
from backend.app.ingest.mca_public import Page

logger = logging.getLogger(__name__)

MCA_HOME = "https://www.mca.gov.in/content/mca/global/en/mca/master-data/MDS/company-master-info.html"
SESSION_TTL_MINUTES = 15   # mca.gov.in default; the page resets the timer on each click


class MCAPublicPlaywrightFetcher:
    """Playwright-backed fetcher. Hands its `__call__` over to the
    `MCAPublicScraper(fetcher=...)` slot."""

    def __init__(self, session_dir: Path | None = None) -> None:
        self.session_dir = Path(
            session_dir or get_settings().mca_public_session_dir
        ).resolve()
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._cookies_path = self.session_dir / "cookies.json"
        self._expiry_path = self.session_dir / "expires_at.txt"
        self._browser = None
        self._context = None

    # --- Public protocol method ------------------------------------------
    async def __call__(self, cin: str, page: Page) -> Any | None:
        """Fetch one MCA page for one CIN.

        Returns None if the CIN doesn't exist OR the session is stale and
        no operator is around to re-bootstrap. The orchestrator treats
        None as 'fall through to next tier'.
        """
        if not self._session_alive():
            logger.warning(
                "MCA Playwright session stale or missing — falling through. "
                "Operator: run `python -m backend.app.ingest.mca_public_playwright "
                "--bootstrap` to refresh."
            )
            return None

        try:
            ctx = await self._ensure_context()
        except Exception as exc:
            logger.warning("MCA Playwright context failed: %s", exc)
            return None

        page_obj = await ctx.new_page()
        try:
            return await _scrape_page(page_obj, cin, page)
        except Exception as exc:
            logger.warning("MCA Playwright scrape failed (%s, %s): %s", cin, page, exc)
            return None
        finally:
            await page_obj.close()
            # Touch the session expiry so consecutive scrapes don't redo
            # the cookie write — Playwright keeps state in-memory anyway.
            self._touch_expiry()

    async def aclose(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None

    # --- Session lifecycle ----------------------------------------------
    def _session_alive(self) -> bool:
        if not self._cookies_path.exists() or not self._expiry_path.exists():
            return False
        try:
            expires_at = datetime.fromisoformat(self._expiry_path.read_text().strip())
        except (ValueError, OSError):
            return False
        return datetime.now(timezone.utc) < expires_at

    def _touch_expiry(self) -> None:
        new_expiry = datetime.now(timezone.utc) + timedelta(minutes=SESSION_TTL_MINUTES)
        self._expiry_path.write_text(new_expiry.isoformat())

    async def _ensure_context(self):
        # Lazy import — playwright is only required in production mode.
        from playwright.async_api import async_playwright

        if self._context is not None:
            return self._context

        pw = await async_playwright().start()
        self._browser = await pw.chromium.launch(headless=True)
        self._context = await self._browser.new_context()

        if self._cookies_path.exists():
            try:
                cookies = json.loads(self._cookies_path.read_text())
                if isinstance(cookies, list):
                    await self._context.add_cookies(cookies)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("MCA cookie file unreadable; bootstrap required: %s", exc)
        return self._context

    # --- One-time bootstrap (operator-driven) ----------------------------
    async def bootstrap(self) -> int:
        """Open a visible browser; operator solves the captcha + completes
        one lookup. We snapshot cookies once the lookup landing page
        renders and persist them. Returns exit code (0 = ok)."""
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(MCA_HOME)
                print(
                    "\n  >>> A browser window has opened.\n"
                    "  >>> 1) Search any CIN you have access to.\n"
                    "  >>> 2) Solve the captcha when prompted.\n"
                    "  >>> 3) Wait until the master-data result page renders.\n"
                    "  >>> 4) Press ENTER here to capture the session cookie.\n"
                )
                await asyncio.get_event_loop().run_in_executor(None, input)
                cookies = await context.cookies()
                self._cookies_path.write_text(json.dumps(cookies, indent=2))
                self._touch_expiry()
                print(f"  >>> Saved {len(cookies)} cookies to {self._cookies_path}")
                print(f"  >>> Session valid for ~{SESSION_TTL_MINUTES} minutes")
                return 0
            finally:
                await browser.close()


# --- Page-specific scraping --------------------------------------------------
async def _scrape_page(page, cin: str, target: Page) -> Any | None:
    """Drive the Playwright page through one of the three flows. The exact
    selectors are pinned to mca.gov.in's V3 master-data templates as of
    2026-05; layout drift breaks here first and the operator runbook
    explains where to look.

    Returns:
      - master:      dict | None
      - signatories: list[dict]
      - charges:     list[dict]
    """
    # NOTE: this module is the rendering boundary — selector strings are
    # the only thing that needs to change when MCA changes their HTML.
    # Keep them at the top of each branch for visibility.
    if target == "master":
        return await _scrape_master(page, cin)
    if target == "signatories":
        return await _scrape_signatories(page, cin)
    if target == "charges":
        return await _scrape_charges(page, cin)
    raise ValueError(f"Unknown page target: {target}")


async def _scrape_master(page, cin: str) -> dict | None:
    await page.goto(MCA_HOME)
    await page.fill('input[name="companyID"]', cin)
    await page.click('button[type="submit"]')
    await page.wait_for_load_state("networkidle", timeout=20_000)
    # If the portal redirected back to home with no result, treat as 404.
    if await page.locator("text=No records found").count() > 0:
        return None
    fields = await page.eval_on_selector_all(
        "table.master-data-table tr",
        """rows => rows.reduce((acc, r) => {
            const k = r.querySelector('th')?.innerText?.trim();
            const v = r.querySelector('td')?.innerText?.trim();
            if (k && v) acc[k] = v;
            return acc;
        }, {})""",
    )
    # Normalise the loose key labels the portal uses to the keys our parser
    # expects.
    def _pick(*labels: str) -> str | None:
        for label in labels:
            for k, v in fields.items():
                if label.lower() in k.lower():
                    return v
        return None

    return {
        "cin": _pick("CIN", "Corporate Identification Number") or cin,
        "companyName": _pick("Company Name", "LLP Name"),
        "dateOfIncorporation": _pick("Date of Incorporation", "Incorporation"),
        "industrial_classification": _pick("Industrial Classification", "Activity"),
        "state": _pick("State"),
        "registered_address": _pick("Address", "Registered Address"),
        "email_id": _pick("Email", "Contact Email"),
    }


async def _scrape_signatories(page, cin: str) -> list[dict]:
    # The Signatory Details page on mca.gov.in is a sibling link from the
    # master-data result. We re-use the same session and follow the link.
    await page.goto(MCA_HOME)
    await page.fill('input[name="companyID"]', cin)
    await page.click('button[type="submit"]')
    await page.wait_for_load_state("networkidle", timeout=20_000)
    try:
        await page.click("text=Signatory Details")
    except Exception:
        return []
    await page.wait_for_load_state("networkidle", timeout=20_000)
    return await page.eval_on_selector_all(
        "table.signatory-details-table tbody tr",
        """rows => rows.map(r => {
            const cells = Array.from(r.querySelectorAll('td')).map(c => c.innerText.trim());
            return {
                din: cells[0] || null,
                name: cells[1] || '',
                designation: (cells[2] || 'director').toLowerCase(),
                appointment_date: cells[3] || null,
                cessation_date: cells[4] || null,
            };
        })""",
    )


async def _scrape_charges(page, cin: str) -> list[dict]:
    await page.goto(MCA_HOME)
    await page.fill('input[name="companyID"]', cin)
    await page.click('button[type="submit"]')
    await page.wait_for_load_state("networkidle", timeout=20_000)
    try:
        await page.click("text=Index of Charges")
    except Exception:
        return []
    await page.wait_for_load_state("networkidle", timeout=20_000)
    return await page.eval_on_selector_all(
        "table.index-of-charges-table tbody tr",
        """rows => rows.map(r => {
            const cells = Array.from(r.querySelectorAll('td')).map(c => c.innerText.trim());
            return {
                charge_id: cells[0] || '',
                lender_name: cells[1] || '',
                amount: (cells[2] || '0').replace(/[^0-9.]/g, ''),
                creation_date: cells[3] || null,
                satisfaction_date: cells[4] || null,
                charge_type: (cells[5] || 'hypothecation').toLowerCase(),
                bank_branch_ifsc: 'UNKNOWN',
            };
        })""",
    )


# --- CLI entrypoint ---------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bootstrap", action="store_true",
        help="Open a visible browser so the operator can solve the MCA "
             "captcha once; persist cookies for ~15 min of headless scraping.",
    )
    parser.add_argument(
        "--session-dir", type=Path, default=None,
        help="Override the session-cache directory (default: MCA_PUBLIC_SESSION_DIR).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    fetcher = MCAPublicPlaywrightFetcher(session_dir=args.session_dir)
    if args.bootstrap:
        return asyncio.run(fetcher.bootstrap())
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
