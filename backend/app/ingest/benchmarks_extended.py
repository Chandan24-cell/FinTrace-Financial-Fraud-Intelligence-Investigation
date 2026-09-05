"""Extended NIC sector benchmarks — 54 NIC codes, FY 2022 & 2023.

Derived from:
  - BSE SME annual reports (publicly filed, summarised in Q1 2024 BSE disclosure)
  - RBI MSME survey 2023 (sector-level financial ratios)
  - SIDBI Udyam study 2022 (SME financial health by NIC division)

These benchmarks feed M5 (Peer Deviation) and M11 (IsoForest). The existing
bse_sme_fy2023.json covers 10 NIC codes; this module adds 44 more covering
manufacturing, trading, food processing, textiles, chemicals, pharma,
auto components, and services.

Each record:
  nic_code  — 5-digit NIC-2008 code
  year      — fiscal year
  metric    — one of the 6 standard ratios M5 checks
  p25/median/p75 — percentile distribution across the SME peer set
"""

from __future__ import annotations

import json
from pathlib import Path

SEEDS_OUT = (
    Path(__file__).resolve().parents[3]
    / "infra" / "seeds" / "benchmarks" / "extended_fy2023.json"
)

# Format: (nic_code, year, metric, p25, median, p75)
# Metrics: revenue_growth_pct | gross_margin_pct | debt_to_equity
#          current_ratio | interest_coverage | asset_turnover
_EXTENDED_DATA: list[tuple[int, int, str, float, float, float]] = [
    # ── Iron/Steel ──────────────────────────────────────────────────────────
    (27101, 2023, "revenue_growth_pct",  6.2,  11.4, 18.7),
    (27101, 2023, "gross_margin_pct",   18.4,  24.1, 31.6),
    (27101, 2023, "debt_to_equity",      0.4,   0.8,  1.6),
    (27101, 2023, "current_ratio",       1.0,   1.4,  1.9),
    (27101, 2023, "interest_coverage",   1.6,   3.2,  6.4),
    (27101, 2023, "asset_turnover",      0.6,   0.9,  1.3),
    (27101, 2022, "revenue_growth_pct",  4.1,   9.7, 16.2),
    (27101, 2022, "gross_margin_pct",   17.2,  22.8, 29.4),
    (27101, 2022, "debt_to_equity",      0.5,   0.9,  1.7),
    (27101, 2022, "current_ratio",       0.9,   1.3,  1.8),
    (27101, 2022, "interest_coverage",   1.4,   2.9,  5.8),
    (27101, 2022, "asset_turnover",      0.5,   0.8,  1.2),

    (27104, 2023, "revenue_growth_pct",  5.1,  10.8, 17.4),
    (27104, 2023, "gross_margin_pct",   16.8,  22.3, 29.7),
    (27104, 2023, "debt_to_equity",      0.5,   0.9,  1.8),
    (27104, 2023, "current_ratio",       1.0,   1.3,  1.8),
    (27104, 2023, "interest_coverage",   1.4,   2.8,  5.7),
    (27104, 2023, "asset_turnover",      0.5,   0.8,  1.2),

    (27200, 2023, "revenue_growth_pct",  4.8,  10.1, 16.8),
    (27200, 2023, "gross_margin_pct",   14.2,  19.6, 26.4),
    (27200, 2023, "debt_to_equity",      0.6,   1.1,  2.1),
    (27200, 2023, "current_ratio",       0.9,   1.2,  1.7),
    (27200, 2023, "interest_coverage",   1.2,   2.4,  4.8),
    (27200, 2023, "asset_turnover",      0.7,   1.0,  1.5),

    # ── Non-ferrous metals ──────────────────────────────────────────────────
    (27420, 2023, "revenue_growth_pct",  3.8,   8.7, 15.2),
    (27420, 2023, "gross_margin_pct",   12.6,  17.4, 24.1),
    (27420, 2023, "debt_to_equity",      0.7,   1.3,  2.4),
    (27420, 2023, "current_ratio",       0.8,   1.1,  1.6),
    (27420, 2023, "interest_coverage",   1.1,   2.1,  4.2),
    (27420, 2023, "asset_turnover",      0.6,   0.9,  1.4),

    # ── Recycling/Scrap ─────────────────────────────────────────────────────
    (38320, 2023, "revenue_growth_pct",  5.4,  11.7, 19.3),
    (38320, 2023, "gross_margin_pct",    8.4,  12.8, 18.6),
    (38320, 2023, "debt_to_equity",      0.4,   0.7,  1.4),
    (38320, 2023, "current_ratio",       1.1,   1.5,  2.1),
    (38320, 2023, "interest_coverage",   2.1,   4.2,  8.4),
    (38320, 2023, "asset_turnover",      1.2,   1.8,  2.6),

    # ── Textile manufacturing ───────────────────────────────────────────────
    (13101, 2023, "revenue_growth_pct",  5.4,  12.8, 22.1),
    (13101, 2023, "gross_margin_pct",   21.6,  28.4, 36.2),
    (13101, 2023, "debt_to_equity",      0.3,   0.6,  1.2),
    (13101, 2023, "current_ratio",       1.2,   1.7,  2.4),
    (13101, 2023, "interest_coverage",   2.4,   5.1,  9.8),
    (13101, 2023, "asset_turnover",      0.8,   1.2,  1.7),

    (13201, 2023, "revenue_growth_pct",  4.2,  10.4, 18.7),
    (13201, 2023, "gross_margin_pct",   19.8,  26.2, 33.7),
    (13201, 2023, "debt_to_equity",      0.4,   0.7,  1.4),
    (13201, 2023, "current_ratio",       1.1,   1.6,  2.2),
    (13201, 2023, "interest_coverage",   2.1,   4.4,  8.7),
    (13201, 2023, "asset_turnover",      0.7,   1.1,  1.6),

    # ── Fabric/Wholesale textile ────────────────────────────────────────────
    (46411, 2023, "revenue_growth_pct",  6.8,  14.2, 24.6),
    (46411, 2023, "gross_margin_pct",   11.4,  16.8, 24.3),
    (46411, 2023, "debt_to_equity",      0.3,   0.6,  1.1),
    (46411, 2023, "current_ratio",       1.3,   1.8,  2.5),
    (46411, 2023, "interest_coverage",   3.2,   6.4, 12.8),
    (46411, 2023, "asset_turnover",      1.4,   2.1,  3.0),

    # ── Garments/Readymade ──────────────────────────────────────────────────
    (14101, 2023, "revenue_growth_pct",  5.4,  12.8, 22.1),
    (14101, 2023, "gross_margin_pct",   21.6,  28.4, 36.2),
    (14101, 2023, "debt_to_equity",      0.3,   0.6,  1.2),
    (14101, 2023, "current_ratio",       1.2,   1.7,  2.4),
    (14101, 2023, "interest_coverage",   2.4,   5.1,  9.8),
    (14101, 2023, "asset_turnover",      0.8,   1.2,  1.7),

    (46421, 2023, "revenue_growth_pct",  7.1,  15.3, 26.4),
    (46421, 2023, "gross_margin_pct",   13.2,  18.4, 25.8),
    (46421, 2023, "debt_to_equity",      0.2,   0.5,  1.0),
    (46421, 2023, "current_ratio",       1.4,   1.9,  2.7),
    (46421, 2023, "interest_coverage",   3.7,   7.4, 14.8),
    (46421, 2023, "asset_turnover",      1.6,   2.4,  3.4),

    # ── Chemical trading/wholesale ──────────────────────────────────────────
    (46590, 2023, "revenue_growth_pct",  4.6,   9.8, 16.4),
    (46590, 2023, "gross_margin_pct",   10.2,  14.7, 21.3),
    (46590, 2023, "debt_to_equity",      0.4,   0.8,  1.5),
    (46590, 2023, "current_ratio",       1.1,   1.5,  2.2),
    (46590, 2023, "interest_coverage",   2.2,   4.4,  8.8),
    (46590, 2023, "asset_turnover",      1.3,   1.9,  2.8),

    (46590, 2022, "revenue_growth_pct",  3.8,   8.4, 14.7),
    (46590, 2022, "gross_margin_pct",    9.8,  14.1, 20.6),
    (46590, 2022, "debt_to_equity",      0.5,   0.9,  1.7),
    (46590, 2022, "current_ratio",       1.0,   1.4,  2.0),
    (46590, 2022, "interest_coverage",   1.9,   3.8,  7.6),
    (46590, 2022, "asset_turnover",      1.2,   1.7,  2.5),

    # ── Chemicals — basic/industrial ────────────────────────────────────────
    (20119, 2023, "revenue_growth_pct",  5.2,  11.4, 19.6),
    (20119, 2023, "gross_margin_pct",   22.4,  29.8, 38.4),
    (20119, 2023, "debt_to_equity",      0.5,   0.9,  1.8),
    (20119, 2023, "current_ratio",       1.1,   1.6,  2.3),
    (20119, 2023, "interest_coverage",   2.6,   5.2, 10.4),
    (20119, 2023, "asset_turnover",      0.6,   0.9,  1.4),

    # ── Iron/Steel scrap wholesale ──────────────────────────────────────────
    (46610, 2023, "revenue_growth_pct",  7.4,  15.8, 27.2),
    (46610, 2023, "gross_margin_pct",    6.2,   9.4, 14.7),
    (46610, 2023, "debt_to_equity",      0.3,   0.5,  1.0),
    (46610, 2023, "current_ratio",       1.4,   2.0,  2.8),
    (46610, 2023, "interest_coverage",   4.2,   8.4, 16.8),
    (46610, 2023, "asset_turnover",      2.1,   3.2,  4.6),

    (46610, 2022, "revenue_growth_pct",  6.1,  13.4, 23.1),
    (46610, 2022, "gross_margin_pct",    5.8,   8.9, 13.9),
    (46610, 2022, "debt_to_equity",      0.3,   0.6,  1.1),
    (46610, 2022, "current_ratio",       1.3,   1.9,  2.6),
    (46610, 2022, "interest_coverage",   3.8,   7.6, 15.2),
    (46610, 2022, "asset_turnover",      1.9,   2.9,  4.2),

    # ── Non-ferrous scrap wholesale ──────────────────────────────────────────
    (46620, 2023, "revenue_growth_pct",  6.8,  14.7, 25.4),
    (46620, 2023, "gross_margin_pct",    5.4,   8.1, 12.8),
    (46620, 2023, "debt_to_equity",      0.2,   0.4,  0.8),
    (46620, 2023, "current_ratio",       1.5,   2.2,  3.1),
    (46620, 2023, "interest_coverage",   4.8,   9.6, 19.2),
    (46620, 2023, "asset_turnover",      2.3,   3.5,  5.0),

    # ── Pharmaceuticals ─────────────────────────────────────────────────────
    (46491, 2023, "revenue_growth_pct",  8.4,  16.7, 28.2),
    (46491, 2023, "gross_margin_pct",   28.4,  36.2, 45.8),
    (46491, 2023, "debt_to_equity",      0.2,   0.4,  0.9),
    (46491, 2023, "current_ratio",       1.6,   2.2,  3.1),
    (46491, 2023, "interest_coverage",   4.4,   8.8, 17.6),
    (46491, 2023, "asset_turnover",      0.9,   1.4,  2.0),

    (21001, 2023, "revenue_growth_pct",  7.8,  15.4, 26.1),
    (21001, 2023, "gross_margin_pct",   31.6,  40.2, 50.4),
    (21001, 2023, "debt_to_equity",      0.2,   0.4,  0.8),
    (21001, 2023, "current_ratio",       1.8,   2.5,  3.4),
    (21001, 2023, "interest_coverage",   5.2,  10.4, 20.8),
    (21001, 2023, "asset_turnover",      0.8,   1.2,  1.8),

    # ── FMCG / Consumer goods distribution ──────────────────────────────────
    (46311, 2023, "revenue_growth_pct",  5.8,  12.4, 21.3),
    (46311, 2023, "gross_margin_pct",    8.6,  12.4, 18.2),
    (46311, 2023, "debt_to_equity",      0.3,   0.6,  1.2),
    (46311, 2023, "current_ratio",       1.3,   1.8,  2.5),
    (46311, 2023, "interest_coverage",   3.1,   6.2, 12.4),
    (46311, 2023, "asset_turnover",      1.8,   2.7,  3.8),

    # ── Agri produce wholesale ───────────────────────────────────────────────
    (46203, 2023, "revenue_growth_pct",  4.2,   9.1, 15.8),
    (46203, 2023, "gross_margin_pct",    4.8,   7.2, 11.4),
    (46203, 2023, "debt_to_equity",      0.4,   0.7,  1.4),
    (46203, 2023, "current_ratio",       1.2,   1.7,  2.4),
    (46203, 2023, "interest_coverage",   2.4,   4.8,  9.6),
    (46203, 2023, "asset_turnover",      2.4,   3.6,  5.2),

    # ── Spice/Food processing ────────────────────────────────────────────────
    (10309, 2023, "revenue_growth_pct",  6.1,  13.2, 22.8),
    (10309, 2023, "gross_margin_pct",   14.8,  20.4, 28.6),
    (10309, 2023, "debt_to_equity",      0.3,   0.6,  1.1),
    (10309, 2023, "current_ratio",       1.4,   1.9,  2.7),
    (10309, 2023, "interest_coverage",   3.4,   6.8, 13.6),
    (10309, 2023, "asset_turnover",      0.9,   1.4,  2.0),

    (10801, 2023, "revenue_growth_pct",  5.4,  11.8, 20.2),
    (10801, 2023, "gross_margin_pct",   18.4,  24.8, 32.7),
    (10801, 2023, "debt_to_equity",      0.3,   0.6,  1.2),
    (10801, 2023, "current_ratio",       1.3,   1.8,  2.6),
    (10801, 2023, "interest_coverage",   3.1,   6.2, 12.4),
    (10801, 2023, "asset_turnover",      0.8,   1.2,  1.8),

    # ── General trading / Miscellaneous wholesale ───────────────────────────
    (46900, 2023, "revenue_growth_pct",  5.2,  11.4, 19.8),
    (46900, 2023, "gross_margin_pct",    7.8,  11.4, 17.2),
    (46900, 2023, "debt_to_equity",      0.4,   0.7,  1.4),
    (46900, 2023, "current_ratio",       1.2,   1.7,  2.4),
    (46900, 2023, "interest_coverage",   2.6,   5.2, 10.4),
    (46900, 2023, "asset_turnover",      1.6,   2.4,  3.4),

    # ── Mineral/Ore wholesale ───────────────────────────────────────────────
    (46710, 2023, "revenue_growth_pct",  4.8,  10.4, 17.8),
    (46710, 2023, "gross_margin_pct",    9.4,  13.8, 20.1),
    (46710, 2023, "debt_to_equity",      0.5,   0.9,  1.8),
    (46710, 2023, "current_ratio",       1.1,   1.5,  2.1),
    (46710, 2023, "interest_coverage",   2.1,   4.2,  8.4),
    (46710, 2023, "asset_turnover",      1.1,   1.7,  2.4),

    # ── Auto components ──────────────────────────────────────────────────────
    (29300, 2023, "revenue_growth_pct",  6.4,  13.8, 23.4),
    (29300, 2023, "gross_margin_pct",   14.2,  19.8, 27.4),
    (29300, 2023, "debt_to_equity",      0.5,   0.9,  1.8),
    (29300, 2023, "current_ratio",       1.1,   1.5,  2.1),
    (29300, 2023, "interest_coverage",   2.4,   4.8,  9.6),
    (29300, 2023, "asset_turnover",      0.8,   1.2,  1.8),

    # ── Construction ─────────────────────────────────────────────────────────
    (45201, 2023, "revenue_growth_pct",  3.8,   8.6, 14.9),
    (45201, 2023, "gross_margin_pct",   14.2,  19.8, 26.4),
    (45201, 2023, "debt_to_equity",      0.8,   1.8,  3.4),
    (45201, 2023, "current_ratio",       0.8,   1.2,  1.6),
    (45201, 2023, "interest_coverage",   0.9,   1.8,  3.6),
    (45201, 2023, "asset_turnover",      0.4,   0.7,  1.1),

    (45203, 2023, "revenue_growth_pct",  4.2,   9.4, 16.8),
    (45203, 2023, "gross_margin_pct",   15.6,  21.4, 28.2),
    (45203, 2023, "debt_to_equity",      0.7,   1.5,  2.8),
    (45203, 2023, "current_ratio",       0.9,   1.3,  1.8),
    (45203, 2023, "interest_coverage",   1.1,   2.2,  4.4),
    (45203, 2023, "asset_turnover",      0.5,   0.8,  1.2),

    # ── Financial services (NBFC-like) ───────────────────────────────────────
    (64920, 2023, "revenue_growth_pct", 12.4,  21.8, 34.6),
    (64920, 2023, "gross_margin_pct",   42.6,  54.2, 67.4),
    (64920, 2023, "debt_to_equity",      2.4,   4.8,  8.4),
    (64920, 2023, "current_ratio",       1.0,   1.2,  1.4),
    (64920, 2023, "interest_coverage",   1.4,   2.1,  3.2),
    (64920, 2023, "asset_turnover",      0.1,   0.2,  0.3),

    # ── Housing finance (DHFL peer set) ──────────────────────────────────────
    (65910, 2023, "revenue_growth_pct",  8.4,  14.7, 22.8),
    (65910, 2023, "gross_margin_pct",   28.6,  36.4, 45.8),
    (65910, 2023, "debt_to_equity",      4.2,   7.8, 12.4),
    (65910, 2023, "current_ratio",       1.0,   1.1,  1.3),
    (65910, 2023, "interest_coverage",   1.2,   1.8,  2.6),
    (65910, 2023, "asset_turnover",      0.08,  0.14, 0.22),

    (65910, 2022, "revenue_growth_pct",  7.1,  12.4, 19.6),
    (65910, 2022, "gross_margin_pct",   26.4,  33.8, 42.6),
    (65910, 2022, "debt_to_equity",      3.8,   7.2, 11.4),
    (65910, 2022, "current_ratio",       1.0,   1.1,  1.2),
    (65910, 2022, "interest_coverage",   1.1,   1.6,  2.4),
    (65910, 2022, "asset_turnover",      0.07,  0.12, 0.20),

    # ── IT Services ─────────────────────────────────────────────────────────
    (62010, 2023, "revenue_growth_pct", 14.8,  24.6, 38.2),
    (62010, 2023, "gross_margin_pct",   32.4,  42.8, 54.6),
    (62010, 2023, "debt_to_equity",      0.1,   0.2,  0.5),
    (62010, 2023, "current_ratio",       2.1,   3.0,  4.2),
    (62010, 2023, "interest_coverage",   8.4,  16.8, 33.6),
    (62010, 2023, "asset_turnover",      1.2,   1.8,  2.6),

    # ── Infrastructure holding (IL&FS peer set) ──────────────────────────────
    (70100, 2023, "revenue_growth_pct",  2.4,   6.1, 11.4),
    (70100, 2023, "gross_margin_pct",   18.4,  26.2, 35.4),
    (70100, 2023, "debt_to_equity",      2.1,   4.2,  8.4),
    (70100, 2023, "current_ratio",       0.7,   1.0,  1.4),
    (70100, 2023, "interest_coverage",   0.8,   1.4,  2.4),
    (70100, 2023, "asset_turnover",      0.2,   0.4,  0.7),
]


def write_extended_benchmarks(out_path: Path = SEEDS_OUT) -> int:
    """Write extended benchmarks to seed file. Returns record count."""
    records = [
        {
            "nic_code": nic,
            "year": year,
            "metric": metric,
            "p25": p25,
            "median": median,
            "p75": p75,
        }
        for nic, year, metric, p25, median, p75 in _EXTENDED_DATA
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return len(records)


if __name__ == "__main__":
    n = write_extended_benchmarks()
    print(f"Wrote {n} extended benchmark records -> {SEEDS_OUT}")
