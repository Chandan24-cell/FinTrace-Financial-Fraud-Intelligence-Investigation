"""GET /narrative/{cin} — Mistral executive summary.

PRD §5.5: numbers come from the graph; the LLM writes prose only. The
route composes the dual-output payload via the same scoring helper
that /analyse uses (so the narrative cites the same numbers the
Dashboard renders), passes a structured evidence-JSON to
`backend.app.narrative.MistralNarrator`, and returns the result —
either a Mistral completion or a deterministic template fallback
when the API key is missing / rate-limited / failed.

Response shape:

    {
      "cin": "U…",
      "summary": "…",                # 90-140 word prose
      "model": "mistral-small-latest" | "template-fallback (…reason)",
      "generated_at": "2026-05-22T…Z",
      "cached": true | false,
      "evidence_hash": "ab12cd34…",  # short SHA — frontend ETag
      "allowed_numbers": [...],      # debug-only, omitted in prod
    }
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from backend.app.auth.deps import require_roles
from backend.app.api.analyse import score_cin_full
from backend.app.api.validators import CIN_PATH
from backend.app.config import get_settings
from backend.app.narrative import get_narrator, serialize_for_narrative

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/narrative",
    tags=["narrative"],
    dependencies=[Depends(require_roles(
        "credit_officer", "investigator", "auditor", "admin"
    ))],
)


@router.get("/{cin}")
async def narrative(
    cin: CIN_PATH,
) -> dict:
    """Compose a Mistral-Flash narrative for the dual-output payload."""
    report = await score_cin_full(cin)
    ni = serialize_for_narrative(report)
    result = await get_narrator().narrate(ni)
    body = {
        "cin": cin,
        "summary": result.summary,
        "model": result.model,
        "generated_at": result.generated_at,
        "cached": result.cached,
        "evidence_hash": result.evidence_hash,
    }
    # Allowed-numbers list — useful for the frontend to verify what the
    # narrator was permitted to cite. Suppressed in prod to keep the
    # payload tight.
    if get_settings().app_env == "dev":
        body["allowed_numbers"] = sorted(ni.allowed_numbers)
    return body
