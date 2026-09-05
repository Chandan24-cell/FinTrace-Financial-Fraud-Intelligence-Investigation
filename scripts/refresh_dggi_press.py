"""Weekly DGGI press release refresh.

Invoked by `.github/workflows/refresh-public-data.yml`. Scrapes the
CBIC press release archive for new ITC carousel busts and writes any
new records to `infra/seeds/itc_carousel/dggi_<id>.json`.

Idempotent: re-running with no new busts produces no diff. Existing
hand-curated ring files (ring.json, mumbai_spider.json, etc.) are
never overwritten — only new dggi_*.json files are added.

Run locally with:

    uv run python -m scripts.refresh_dggi_press
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from backend.app.ingest.dggi_press import fetch_recent_dggi_busts, filter_to_carousel

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = REPO_ROOT / "infra" / "seeds" / "itc_carousel"


async def _run() -> int:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    records = await fetch_recent_dggi_busts(max_releases=30)
    carousels = filter_to_carousel(records)
    written = 0
    for rec in carousels:
        slug = rec.url.rsplit("/", 1)[-1].split(".")[0][:24]
        out_path = TARGET_DIR / f"dggi_{slug}.json"
        payload = rec.to_seed_entry()
        existing = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None
        if existing == payload:
            continue
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        written += 1
        logger.info("Wrote %s", out_path.relative_to(REPO_ROOT))
    logger.info("DGGI refresh: %d records fetched, %d new files written", len(carousels), written)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(asyncio.run(_run()))
