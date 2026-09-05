"""DataCompletenessScore — PRD §7.1.

| Data Available                        | DataConfidence |
|---------------------------------------|----------------|
| MCA master only — no financials       | 25%            |
| MCA + 1 year financials               | 45%            |
| MCA + 2+ years financials             | 65%            |
| Above + GST upload                    | 80%            |
| Above + bank statements               | 92%            |

PRD §7 invariant: NEVER output FraudRiskScore without DataConfidence.
"""

from __future__ import annotations

from backend.app.ingest.schemas import CompanyBundle


def compute_data_confidence(bundle: CompanyBundle) -> int:
    """Return DataConfidence as an integer percentage 25–92."""
    n_financials = len(bundle.financials)
    has_gst = bundle.has_gst_upload
    has_bank = bundle.has_bank_upload

    if n_financials == 0:
        return 25

    if n_financials == 1:
        return 45

    # n_financials >= 2
    if not has_gst:
        return 65
    if not has_bank:
        return 80
    return 92
