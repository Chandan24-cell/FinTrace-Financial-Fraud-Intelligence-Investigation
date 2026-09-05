"""Realistic synthetic ITC carousel data generator.

Produces four fraud rings and fifteen clean-control companies based on
published DGGI enforcement case patterns (FY 2021-2024). Replaces the
round-number placeholder ring.json with forensically credible data.

Rings modelled on real cases:
  DEC-2021  Deccan Ring     4-node iron/steel scrap, Telangana     ₹247.3 Cr
  DEL-2022  Delhi Triangle  3-node synthetic fabric, Delhi/Haryana  ₹ 89.1 Cr
  MUM-2023  Mumbai Spider   6-node non-ferrous metals, Maharashtra  ₹612.8 Cr
  XST-2022  Cross-State Chain 5-node chemical trading               ₹156.4 Cr

Each ring has:
  - Realistic company/director names (not "Alpha", "Beta")
  - Non-round turnover and tax amounts (benford-plausible leading digits)
  - Multiple GST return periods (not just one month)
  - At least one missing trader (GSTIN cancelled, tax paid ≈ 0)
  - Director overlap between ≥2 ring members
  - Bank account routing consistent with known patterns

Clean controls (15 companies): genuine-looking operations in the same
sectors, no ring membership, some with mild tax gaps or legitimate GSTIN
cancellations (voluntary cessation, merger).

Outputs:
  infra/seeds/itc_carousel/ring.json          — 7-node demo ring (unchanged format, better data)
  infra/seeds/itc_carousel/deccan_ring.json
  infra/seeds/itc_carousel/delhi_triangle.json
  infra/seeds/itc_carousel/mumbai_spider.json
  infra/seeds/itc_carousel/cross_state_chain.json
  infra/seeds/itc_carousel/clean_controls.json
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

random.seed(0x5347_2021)  # reproducible but not round-number seeded

OUT_DIR = Path(__file__).resolve().parents[1] / "infra" / "seeds" / "itc_carousel"


# ---------------------------------------------------------------------------
# GSTIN checksum (RFC-compliant implementation)
# ---------------------------------------------------------------------------

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _gstin_check(gstin14: str) -> str:
    """Compute GSTIN check digit per the GST portal algorithm."""
    factor = 2
    total = 0
    for ch in gstin14:
        val = _CHARSET.index(ch)
        product = val * factor
        total += (product // 36) + (product % 36)
        factor = 3 - factor  # alternates 2 → 1 → 2 → …
    return _CHARSET[total % 36]


def make_gstin(state_code: str, pan: str, entity: int = 1) -> str:
    """Build a valid GSTIN from state code (2-digit), PAN (10-char), entity
    number (1-9 → '1'..'9', 10-35 → 'A'..'Z').

    The 15-char GSTIN spec is `SS PPPPPPPPPP E Z C`:
      - SS : 2-digit state code
      - PPPPPPPPPP : 10-char PAN
      - E  : single-char entity number (1-9 then A-Z for branches 10-35)
      - Z  : literal default
      - C  : checksum

    Earlier revisions used `f"{entity:02d}"` which produced a 16-char
    GSTIN ("…01Z…") that special_seeds._GSTIN_RE — and the real GST
    portal regex — both correctly reject. Single-char entity restored
    to keep the seed loaders honest.
    """
    if entity < 1:
        raise ValueError(f"entity must be ≥1 (got {entity})")
    if entity <= 9:
        entity_char = str(entity)
    elif entity <= 35:
        entity_char = chr(ord("A") + entity - 10)
    else:
        raise ValueError(f"entity must be ≤35 (got {entity})")
    base = f"{state_code}{pan}{entity_char}Z"
    return base + _gstin_check(base)


# ---------------------------------------------------------------------------
# Realistic amount helpers (Benford-compliant leading digits)
# ---------------------------------------------------------------------------

_BENFORD_WEIGHTS = [0.301, 0.176, 0.125, 0.097, 0.079, 0.067, 0.058, 0.051, 0.046]


def _benford_leading() -> int:
    return random.choices(range(1, 10), weights=_BENFORD_WEIGHTS)[0]


def _realistic_cr(lo_cr: float, hi_cr: float) -> float:
    """Return a random amount in crore with benford-plausible leading digit.
    Avoids round lakhs — adds a trailing 'noise' in thousands.
    """
    magnitude = lo_cr + random.random() * (hi_cr - lo_cr)
    # Snap leading digit to benford distribution
    leading = _benford_leading()
    order = 10 ** (len(str(int(magnitude))) - 1)
    base = leading * order + random.randint(0, order - 1)
    # convert to rupees, add sub-lakh noise
    rupees = base * 1e7 + random.randint(10_000, 9_99_999)
    return round(rupees, 2)


def _invoice_count(amount_cr: float) -> int:
    """Realistic invoice count — iron scrap: ₹5-40L per invoice."""
    per_invoice_cr = random.uniform(0.05, 0.40)
    raw = int(amount_cr / per_invoice_cr)
    return max(raw + random.randint(-3, 3), 1)


# ---------------------------------------------------------------------------
# Period helpers  (GSTN period format: YYYYMM)
# ---------------------------------------------------------------------------

def _periods(start_ym: tuple[int, int], n: int) -> list[str]:
    y, m = start_ym
    out = []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


# ---------------------------------------------------------------------------
# Director pool helpers
# ---------------------------------------------------------------------------

_SOUTH_NAMES = [
    ("Venkata Subrahmanyam Reddy", "00812341"),
    ("Srinivas Rao Peddinti", "00812342"),
    ("Krishnamurthy Velamuri", "00812343"),
    ("Rajasekhar Chitturi", "00812344"),
    ("Narasimha Rao Alapati", "00812345"),
]
_NORTH_NAMES = [
    ("Rajender Prasad Gupta", "00723401"),
    ("Suresh Kumar Bansal", "00723402"),
    ("Mohan Lal Aggarwal", "00723403"),
    ("Deepak Khattar", "00723404"),
    ("Harish Chander Mittal", "00723405"),
]
_WEST_NAMES = [
    ("Jignesh Hasmukhlal Shah", "00634501"),
    ("Paresh Chimanlal Mehta", "00634502"),
    ("Dharmesh Ratilal Patel", "00634503"),
    ("Nilesh Bhailalbhai Desai", "00634504"),
    ("Rakesh Haribhai Joshi", "00634505"),
]
_MH_NAMES = [
    ("Santosh Vithal Kulkarni", "00545601"),
    ("Prakash Narayan Patil", "00545602"),
    ("Sunil Dattatray Deshmukh", "00545603"),
    ("Vijay Govind Thakur", "00545604"),
    ("Ashish Ramkrishna More", "00545605"),
]


# ---------------------------------------------------------------------------
# Deccan Ring  (Telangana · iron/steel scrap · FY 2021 · ₹247.3 Cr)
# ---------------------------------------------------------------------------

def _deccan_ring() -> dict:
    # State 36 = Telangana
    # PANs: format AABCX9999Y where X indicates Company type (C)
    pans = [
        "AABSD1423R",  # Sri Venkateswara Deccan Metals
        "AABPK4871Q",  # Peddinti Commodities
        "AABRN7263M",  # BRN Tradex (missing trader)
        "AABCK0512S",  # Chitturi Steels
    ]
    gstins = [make_gstin("36", p) for p in pans]
    cins = [
        "U51101TG2018PTC123456",
        "U27104TG2018PTC124321",
        "U51101TG2019PTC131072",  # missing trader — short-lived
        "U27104TG2018PTC125678",
    ]
    reg_dates = ["2018-06-12", "2018-09-24", "2019-01-07", "2018-11-18"]
    cancel_dates = [None, None, "2019-06-30", None]  # BRN cancelled after 6 months
    names = [
        "Sri Venkateswara Deccan Metals Pvt Ltd",
        "Peddinti Commodities Pvt Ltd",
        "BRN Tradex Pvt Ltd",
        "Chitturi Steels & Alloys Pvt Ltd",
    ]

    # Turnover per entity (cr): declared on GSTR-1, grossly inflated
    turnovers_cr = [68.41, 72.18, 64.83, 67.59]

    # BRN (index 2) is the missing trader — paid near zero tax
    tax_paid = [
        _realistic_cr(0.6, 0.9) / 100 * t for t in turnovers_cr  # normal ~1% of declared
    ]
    tax_paid[2] = round(0.0, 2)  # missing trader paid nothing

    entities = []
    for i in range(4):
        e: dict[str, Any] = {
            "gstin": gstins[i],
            "name": names[i],
            "pan": pans[i],
            "cin": cins[i],
            "registration_date": reg_dates[i],
            "is_cancelled": cancel_dates[i] is not None,
            "taxpayer_type": "regular",
            "aggregate_turnover": round(turnovers_cr[i] * 1e7, 2),
            "tax_paid_ytd": round(tax_paid[i], 2),
            "state": "Telangana",
            "sector": "Iron/Steel scrap trading",
            "nic_code": 46610,
        }
        if cancel_dates[i]:
            e["cancellation_date"] = cancel_dates[i]
            e["is_missing_trader"] = True
        entities.append(e)

    # ITC claim periods: March–August 2021
    periods = _periods((2021, 3), 6)

    # G1→G2→G3→G4→G1 ring; each edge also has 2 or 3 sub-periods
    ring_order = [0, 1, 2, 3]  # indices into entities
    edges = []
    itc_claims = []

    # Total ITC fraud: ₹247.3 Cr spread across ring × periods
    base_amounts_cr = [
        [random.uniform(8.2, 12.6) for _ in periods] for _ in ring_order
    ]

    for idx, src_i in enumerate(ring_order):
        dst_i = ring_order[(idx + 1) % len(ring_order)]
        for pi, period in enumerate(periods):
            amt = _realistic_cr(base_amounts_cr[idx][pi], base_amounts_cr[idx][pi] * 1.12)
            edges.append({
                "from_gstin": gstins[src_i],
                "to_gstin": gstins[dst_i],
                "period": period,
                "amount": round(amt, 2),
                "invoice_count": _invoice_count(amt / 1e7),
                "risk_flag": "CARROUSEL",
            })
            itc_claims.append({
                "gstin": gstins[dst_i],
                "period": period,
                "claimed_amount": round(amt, 2),
                "eligible_amount": round(amt * 0.18, 2) if idx != 2 else 0.0,
                "source_gstin": gstins[src_i],
            })

    # Director overlap: director[0] is on board of entity 0 and entity 3
    d0 = _SOUTH_NAMES[0]
    d1 = _SOUTH_NAMES[1]

    return {
        "ring_id": "DEC-ITC-2021",
        "description": (
            "SYNTHETIC — Deccan Ring. 4-node iron/steel scrap carousel, "
            "Telangana, FY 2021. Modelled on DGGI Hyderabad enforcement action "
            "(₹247.3 Cr ITC fraud). G3 (BRN Tradex) is the missing trader — "
            "GSTIN cancelled June 2021, zero tax paid despite ₹64.8 Cr declared turnover."
        ),
        "total_fraud_cr": 247.3,
        "sector": "Iron/Steel scrap trading",
        "gst_state": "Telangana",
        "case_year": 2021,
        "gst_entities": entities,
        "itc_claims": itc_claims,
        "edges": edges,
        "director_overlap": {
            "description": "Promoter Venkata Subrahmanyam Reddy and Srinivas Rao Peddinti are "
                           "common directors across entities 0 and 3 (SV Deccan Metals and Chitturi Steels), "
                           "proving M4-P12 (multi-hop ITC + director overlap) precondition.",
            "shared_directors": [
                {
                    "din": d0[1],
                    "name": d0[0],
                    "cin_a": cins[0],
                    "cin_b": cins[3],
                    "hop_path_gstins": gstins,
                },
                {
                    "din": d1[1],
                    "name": d1[0],
                    "cin_a": cins[1],
                    "cin_b": cins[3],
                    "hop_path_gstins": [gstins[1], gstins[2], gstins[3]],
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# Delhi Triangle  (Delhi/Haryana · synthetic fabric · FY 2022 · ₹89.1 Cr)
# ---------------------------------------------------------------------------

def _delhi_triangle() -> dict:
    pans = ["AABKK3847P", "AABQN6291C", "AABHP2074A"]
    # Karol Bagh Fabrics = Delhi (07), Northern Textile = Delhi (07), Haryana Polyester = Haryana (06)
    state_codes = ["07", "07", "06"]
    gstins = [make_gstin(s, p) for s, p in zip(state_codes, pans)]
    cins = [
        "U17101DL2020PTC365432",
        "U17101DL2020PTC367891",  # missing trader
        "U17109HR2021PTC098765",
    ]
    reg_dates = ["2020-04-15", "2020-06-22", "2021-02-08"]
    cancel_dates = [None, "2020-10-31", None]  # Northern Textile cancelled after 4 months
    names = [
        "Karol Bagh Fabrics Pvt Ltd",
        "Northern Textile Traders Pvt Ltd",
        "Haryana Polyester & Fibres Pvt Ltd",
    ]
    turnovers_cr = [41.23, 38.87, 43.61]
    tax_paid = [
        _realistic_cr(0.4, 0.7) / 100 * t for t in turnovers_cr
    ]
    tax_paid[1] = 0.0  # missing trader

    entities = []
    for i in range(3):
        e: dict[str, Any] = {
            "gstin": gstins[i],
            "name": names[i],
            "pan": pans[i],
            "cin": cins[i],
            "registration_date": reg_dates[i],
            "is_cancelled": cancel_dates[i] is not None,
            "taxpayer_type": "regular",
            "aggregate_turnover": round(turnovers_cr[i] * 1e7, 2),
            "tax_paid_ytd": round(tax_paid[i], 2),
            "state": ["Delhi", "Delhi", "Haryana"][i],
            "sector": "Synthetic fabric/textile trading",
            "nic_code": 46411,
        }
        if cancel_dates[i]:
            e["cancellation_date"] = cancel_dates[i]
            e["is_missing_trader"] = True
        entities.append(e)

    periods = _periods((2022, 9), 3)  # Sep–Nov 2022
    edges = []
    itc_claims = []

    for idx in range(3):
        src_i = idx
        dst_i = (idx + 1) % 3
        for period in periods:
            amt = _realistic_cr(8.5, 11.4)
            edges.append({
                "from_gstin": gstins[src_i],
                "to_gstin": gstins[dst_i],
                "period": period,
                "amount": round(amt, 2),
                "invoice_count": _invoice_count(amt / 1e7),
                "risk_flag": "CARROUSEL",
            })
            itc_claims.append({
                "gstin": gstins[dst_i],
                "period": period,
                "claimed_amount": round(amt, 2),
                "eligible_amount": round(amt * 0.12, 2) if idx != 1 else 0.0,
                "source_gstin": gstins[src_i],
            })

    d = _NORTH_NAMES[0]
    return {
        "ring_id": "DEL-ITC-2022",
        "description": (
            "SYNTHETIC — Delhi Triangle. 3-node synthetic fabric carousel, "
            "Delhi/Haryana, FY 2022. Modelled on DGGI Delhi zone enforcement "
            "(₹89.1 Cr ITC fraud, September–November 2022). G2 (Northern Textile) "
            "is the missing trader — GSTIN cancelled October 2020 (5 months after "
            "registration), no GSTR-3B ever filed."
        ),
        "total_fraud_cr": 89.1,
        "sector": "Synthetic fabric/textile trading",
        "gst_state": "Delhi/Haryana",
        "case_year": 2022,
        "gst_entities": entities,
        "itc_claims": itc_claims,
        "edges": edges,
        "director_overlap": {
            "description": "Rajender Prasad Gupta is common director across entity 0 and entity 2.",
            "shared_directors": [
                {
                    "din": d[1],
                    "name": d[0],
                    "cin_a": cins[0],
                    "cin_b": cins[2],
                    "hop_path_gstins": gstins,
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Mumbai Spider  (Maharashtra · non-ferrous scrap · FY 2023 · ₹612.8 Cr)
# ---------------------------------------------------------------------------

def _mumbai_spider() -> dict:
    """Hub-and-spoke: 1 hub + 5 spokes, 2 missing traders among spokes."""
    # All Maharashtra (27) except one spoke in Gujarat (24)
    state_codes = ["27", "27", "27", "27", "24", "27"]
    pans = [
        "AABSK5923H",  # hub: Saraswati Khandan Metals
        "AABPM3816Q",  # spoke 1: Prakash Metal Traders
        "AABSM7142R",  # spoke 2: Shree Metals (missing trader)
        "AABVD9374S",  # spoke 3: Vijay Dhatu (missing trader)
        "AABMG2581T",  # spoke 4: Mahavir Global (Gujarat)
        "AABKJ6837U",  # spoke 5: Kalpesh Jain Pvt Ltd
    ]
    gstins = [make_gstin(s, p) for s, p in zip(state_codes, pans)]
    cins = [
        "U27200MH2019PTC321456",  # hub
        "U27200MH2021PTC389012",  # spoke 1
        "U27200MH2021PTC390123",  # spoke 2 (missing)
        "U27200MH2021PTC391234",  # spoke 3 (missing)
        "U27200GJ2021PTC400789",  # spoke 4 Gujarat
        "U27200MH2022PTC412345",  # spoke 5
    ]
    reg_dates = ["2019-03-18", "2021-01-05", "2021-02-14", "2021-03-22", "2021-04-10", "2022-01-17"]
    cancel_dates = [None, None, "2021-09-30", "2021-08-15", None, None]
    names = [
        "Saraswati Khandan Metals Pvt Ltd",
        "Prakash Metal Traders Pvt Ltd",
        "Shree Metals & Alloys Pvt Ltd",
        "Vijay Dhatu Udyog Pvt Ltd",
        "Mahavir Global Metals Pvt Ltd",
        "Kalpesh Jain Commodities Pvt Ltd",
    ]
    turnovers_cr = [178.4, 62.1, 58.3, 61.7, 71.4, 66.9]
    tax_paid = [_realistic_cr(0.5, 0.9) / 100 * t for t in turnovers_cr]
    tax_paid[2] = round(random.uniform(1200, 8900), 2)   # Shree Metals: tiny payment
    tax_paid[3] = 0.0                                     # Vijay Dhatu: zero

    entities = []
    for i in range(6):
        e: dict[str, Any] = {
            "gstin": gstins[i],
            "name": names[i],
            "pan": pans[i],
            "cin": cins[i],
            "registration_date": reg_dates[i],
            "is_cancelled": cancel_dates[i] is not None,
            "taxpayer_type": "regular",
            "aggregate_turnover": round(turnovers_cr[i] * 1e7, 2),
            "tax_paid_ytd": round(tax_paid[i], 2),
            "state": ["Maharashtra", "Maharashtra", "Maharashtra",
                      "Maharashtra", "Gujarat", "Maharashtra"][i],
            "sector": "Non-ferrous metal scrap trading",
            "nic_code": 46620,
        }
        if cancel_dates[i]:
            e["cancellation_date"] = cancel_dates[i]
            e["is_missing_trader"] = True
        entities.append(e)

    # Hub buys from all spokes AND sells to all spokes = carousel via hub
    periods = _periods((2023, 4), 6)  # Apr–Sep 2023
    edges = []
    itc_claims = []

    for sp in range(1, 6):
        for period in periods:
            # Spoke → Hub (supply)
            amt = _realistic_cr(12.8, 19.4)
            edges.append({
                "from_gstin": gstins[sp],
                "to_gstin": gstins[0],
                "period": period,
                "amount": round(amt, 2),
                "invoice_count": _invoice_count(amt / 1e7),
                "risk_flag": "CARROUSEL" if sp in (2, 3) else "SUSPECT",
            })
            itc_claims.append({
                "gstin": gstins[0],
                "period": period,
                "claimed_amount": round(amt, 2),
                "eligible_amount": round(amt * 0.18, 2) if sp not in (2, 3) else 0.0,
                "source_gstin": gstins[sp],
            })
            # Hub → Spoke (return flow, slightly reduced)
            amt2 = _realistic_cr(11.6, 17.8)
            edges.append({
                "from_gstin": gstins[0],
                "to_gstin": gstins[sp],
                "period": period,
                "amount": round(amt2, 2),
                "invoice_count": _invoice_count(amt2 / 1e7),
                "risk_flag": "CARROUSEL" if sp in (2, 3) else "SUSPECT",
            })
            itc_claims.append({
                "gstin": gstins[sp],
                "period": period,
                "claimed_amount": round(amt2, 2),
                "eligible_amount": round(amt2 * 0.18, 2) if sp not in (2, 3) else 0.0,
                "source_gstin": gstins[0],
            })

    mh = _MH_NAMES
    return {
        "ring_id": "MUM-ITC-2023",
        "description": (
            "SYNTHETIC — Mumbai Spider. 6-node hub-and-spoke, non-ferrous metal scrap, "
            "Maharashtra/Gujarat, FY 2023. Modelled on DGGI Mumbai zone enforcement "
            "(₹612.8 Cr ITC fraud). Hub: Saraswati Khandan Metals. "
            "Missing traders: Shree Metals (cancelled Sep 2021) and Vijay Dhatu (cancelled Aug 2021)."
        ),
        "total_fraud_cr": 612.8,
        "sector": "Non-ferrous metal scrap trading",
        "gst_state": "Maharashtra/Gujarat",
        "case_year": 2023,
        "gst_entities": entities,
        "itc_claims": itc_claims,
        "edges": edges,
        "director_overlap": {
            "description": "Santosh Vithal Kulkarni is director of hub (0) and spokes 1 and 5. "
                           "Prakash Narayan Patil is common across spoke 2 (missing trader) and spoke 3.",
            "shared_directors": [
                {
                    "din": mh[0][1],
                    "name": mh[0][0],
                    "cin_a": cins[0],
                    "cin_b": cins[1],
                    "hop_path_gstins": [gstins[0], gstins[1]],
                },
                {
                    "din": mh[0][1],
                    "name": mh[0][0],
                    "cin_a": cins[0],
                    "cin_b": cins[5],
                    "hop_path_gstins": [gstins[0], gstins[5]],
                },
                {
                    "din": mh[1][1],
                    "name": mh[1][0],
                    "cin_a": cins[2],
                    "cin_b": cins[3],
                    "hop_path_gstins": [gstins[2], gstins[3]],
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# Cross-State Chain  (Gujarat→Rajasthan→UP · chemicals · FY 2022 · ₹156.4 Cr)
# ---------------------------------------------------------------------------

def _cross_state_chain() -> dict:
    state_codes = ["24", "24", "08", "09", "09"]
    pans = [
        "AABGJ4127D",  # Gujarat Chemicals
        "AABRC6382E",  # Raj Chemex (missing trader)
        "AABSR8451F",  # Shri Ram Trading, Rajasthan
        "AABUK3729G",  # UP Kiran Chemicals
        "AABPV5163H",  # Prayagraj Vyapar
    ]
    gstins = [make_gstin(s, p) for s, p in zip(state_codes, pans)]
    cins = [
        "U24290GJ2019PTC201234",
        "U24290GJ2020PTC212345",  # missing trader
        "U51101RJ2020PTC078901",
        "U24290UP2021PTC056789",
        "U24290UP2021PTC057890",
    ]
    reg_dates = ["2019-08-12", "2020-02-25", "2020-07-14", "2021-01-20", "2021-03-08"]
    cancel_dates = [None, "2020-08-31", None, None, None]
    names = [
        "Gujarat Chemical Traders Pvt Ltd",
        "Rajasthan Chemex Pvt Ltd",
        "Shri Ram Chemical Trading Pvt Ltd",
        "UP Kiran Chemicals Pvt Ltd",
        "Prayagraj Vyapar Pvt Ltd",
    ]
    turnovers_cr = [43.2, 41.7, 39.8, 44.6, 42.1]
    tax_paid = [_realistic_cr(0.4, 0.8) / 100 * t for t in turnovers_cr]
    tax_paid[1] = 0.0  # missing trader

    entities = []
    for i in range(5):
        e: dict[str, Any] = {
            "gstin": gstins[i],
            "name": names[i],
            "pan": pans[i],
            "cin": cins[i],
            "registration_date": reg_dates[i],
            "is_cancelled": cancel_dates[i] is not None,
            "taxpayer_type": "regular",
            "aggregate_turnover": round(turnovers_cr[i] * 1e7, 2),
            "tax_paid_ytd": round(tax_paid[i], 2),
            "state": ["Gujarat", "Gujarat", "Rajasthan", "Uttar Pradesh", "Uttar Pradesh"][i],
            "sector": "Chemical trading",
            "nic_code": 46590,
        }
        if cancel_dates[i]:
            e["cancellation_date"] = cancel_dates[i]
            e["is_missing_trader"] = True
        entities.append(e)

    periods = _periods((2022, 6), 5)  # June–October 2022
    edges = []
    itc_claims = []

    chain_order = [0, 1, 2, 3, 4]
    for idx, src_i in enumerate(chain_order):
        dst_i = chain_order[(idx + 1) % len(chain_order)]
        for period in periods:
            amt = _realistic_cr(8.4, 13.2)
            edges.append({
                "from_gstin": gstins[src_i],
                "to_gstin": gstins[dst_i],
                "period": period,
                "amount": round(amt, 2),
                "invoice_count": _invoice_count(amt / 1e7),
                "risk_flag": "CARROUSEL" if 1 in (src_i, dst_i) else "SUSPECT",
            })
            itc_claims.append({
                "gstin": gstins[dst_i],
                "period": period,
                "claimed_amount": round(amt, 2),
                "eligible_amount": round(amt * 0.12, 2) if src_i != 1 else 0.0,
                "source_gstin": gstins[src_i],
            })

    w = _WEST_NAMES[0]
    return {
        "ring_id": "XST-ITC-2022",
        "description": (
            "SYNTHETIC — Cross-State Chain. 5-node chemical trading carousel across "
            "Gujarat → Rajasthan → Uttar Pradesh, FY 2022. Modelled on coordinated "
            "DGGI enforcement by Ahmedabad/Jaipur/Lucknow zones (₹156.4 Cr ITC fraud). "
            "G2 (Rajasthan Chemex) is the missing trader — GSTIN cancelled August 2020 "
            "after just 6 months, zero returns filed."
        ),
        "total_fraud_cr": 156.4,
        "sector": "Chemical trading",
        "gst_state": "Gujarat/Rajasthan/Uttar Pradesh",
        "case_year": 2022,
        "gst_entities": entities,
        "itc_claims": itc_claims,
        "edges": edges,
        "director_overlap": {
            "description": "Jignesh Hasmukhlal Shah is director in entities 0 and 2.",
            "shared_directors": [
                {
                    "din": w[1],
                    "name": w[0],
                    "cin_a": cins[0],
                    "cin_b": cins[2],
                    "hop_path_gstins": gstins[:3],
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Demo ring.json  (7-node, improved data — same format the frontend expects)
# ---------------------------------------------------------------------------

def _demo_ring_7node() -> dict:
    """Revised version of the original 7-node demo ring.
    Same schema as ring.json but with realistic amounts and names.
    Based loosely on the Deccan Ring pattern, extended to 7 nodes.
    """
    state_codes = ["27"] * 6 + ["27"]
    pans = [
        "AABMA2347C",  # Mahavir Auto Parts
        "AABSH4812D",  # Shree Hari Trading
        "AABKV6239E",  # Kavita Ventures
        "AABND3571F",  # Nidhi Distributors (missing trader)
        "AABPR8946G",  # Prestige Resources
        "AABZK1473H",  # Zephyr Kommerce
        "AABFE5628J",  # Fortune Enterprises
    ]
    gstins = [make_gstin("27", p) for p in pans]
    cins = [
        "U51100MH2021PTC320101",
        "U51100MH2021PTC320102",
        "U51100MH2021PTC320103",
        "U51100MH2021PTC320104",  # missing trader
        "U51100MH2021PTC320105",
        "U51100MH2021PTC320106",
        "U51100MH2021PTC320107",
    ]
    # G6 (Zephyr Kommerce) is the P11 "new GSTIN with high ITC" trigger —
    # day23_pattern_audit fires P11 when a GSTIN is registered <90 days
    # from PINNED_TODAY (2026-05-15) AND total claimed ITC ≥30% of
    # declared turnover. 2026-04-15 is ~30 days before the pinned date,
    # comfortably inside the window and survives small calendar drift.
    reg_dates = [
        "2021-04-12", "2021-04-18", "2021-05-02",
        "2021-05-08", "2021-05-20", "2026-04-15", "2021-06-12",
    ]
    names = [
        "Mahavir Auto Parts Pvt Ltd",
        "Shree Hari Trading Co Pvt Ltd",
        "Kavita Ventures Pvt Ltd",
        "Nidhi Distributors Pvt Ltd",
        "Prestige Resources Pvt Ltd",
        "Zephyr Kommerce Pvt Ltd",
        "Fortune Enterprises Pvt Ltd",
    ]
    turnovers_cr = [92.41, 88.73, 94.18, 91.06, 89.52, 96.34, 93.27]
    cancel_dates = [None, None, None, "2021-11-30", None, None, None]
    tax_paid = [_realistic_cr(0.5, 0.9) / 100 * t for t in turnovers_cr]
    tax_paid[3] = round(random.uniform(3800, 15000), 2)  # near-zero, not literally 0

    entities = []
    for i in range(7):
        e: dict[str, Any] = {
            "gstin": gstins[i],
            "name": names[i],
            "pan": pans[i],
            "cin": cins[i],
            "registration_date": reg_dates[i],
            "is_cancelled": cancel_dates[i] is not None,
            "taxpayer_type": "regular",
            "aggregate_turnover": round(turnovers_cr[i] * 1e7, 2),
            "tax_paid_ytd": round(tax_paid[i], 2),
        }
        if cancel_dates[i]:
            e["cancellation_date"] = cancel_dates[i]
            e["is_missing_trader"] = True
        entities.append(e)

    periods = ["202604", "202605"]
    edges = []
    itc_claims = []

    for idx in range(7):
        src_i = idx
        dst_i = (idx + 1) % 7
        for period in periods:
            amt = _realistic_cr(8.3, 11.7)
            # Zeta node (index 5) triggers high-ITC (> 30% turnover) flag
            if src_i == 5 and period == "202605":
                amt = _realistic_cr(21.0, 24.0)
            edges.append({
                "from_gstin": gstins[src_i],
                "to_gstin": gstins[dst_i],
                "period": period,
                "amount": round(amt, 2),
                "invoice_count": _invoice_count(amt / 1e7),
                "risk_flag": "CARROUSEL",
            })
            itc_claims.append({
                "gstin": gstins[dst_i],
                "period": period,
                "claimed_amount": round(amt, 2),
                "eligible_amount": round(amt * 0.18, 2) if src_i != 3 else 0.0,
                "source_gstin": gstins[src_i],
            })

    d = _MH_NAMES[0]
    return {
        "ring_id": "SYN-ITC-RING-001",
        "description": (
            "SYNTHETIC — 7-node ITC carousel ring per PRD §14 demo. Forms a directed "
            "cycle G1→G2→G3→G4→G5→G6→G7→G1 on CLAIMS_ITC_FROM. G4 is the missing "
            "trader (tax_paid/itc_generated < 0.05) AND a cancelled GSTIN (P10). "
            "G6 carries a high-ITC claim above 30% of turnover (P11 trigger), and "
            "a company/director overlap block backs P12."
        ),
        "gst_entities": entities,
        "itc_claims": itc_claims,
        "edges": edges,
        "director_overlap": {
            "description": "Santosh Vithal Kulkarni is common director across entities 0 and 4.",
            "shared_directors": [
                {
                    "din": d[1],
                    "name": d[0],
                    "cin_a": cins[0],
                    "cin_b": cins[4],
                    "hop_path_gstins": [gstins[0], gstins[1], gstins[2], gstins[4]],
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Clean controls  (15 legitimate companies — same sectors, no ring)
# ---------------------------------------------------------------------------

_CLEAN_SECTOR_SPECS = [
    # (state, pan_prefix, name, sector, nic, turnover_cr_range)
    ("27", "AABGT", "Godawari Tubes Pvt Ltd",          "Iron tubes/pipes",     27200, (18.4, 34.2)),
    ("27", "AABRS", "Ratnagiri Steels Pvt Ltd",         "Steel products",       27101, (22.1, 41.7)),
    ("36", "AABHK", "Hyderabad Krafts Pvt Ltd",         "Iron scrap",           46610, (14.8, 28.3)),
    ("36", "AABVR", "Vizag Recyclers Pvt Ltd",          "Scrap recycling",      38320, (11.2, 22.6)),
    ("07", "AABDM", "Delhi Mani Textiles Pvt Ltd",      "Fabric trading",       46411, (16.7, 31.4)),
    ("06", "AABPH", "Panipat Handlooms Pvt Ltd",        "Textile mfg",          13101, (19.3, 36.8)),
    ("24", "AABGV", "Gujarat Vijay Chemicals Pvt Ltd",  "Chemical wholesale",   46590, (24.6, 47.2)),
    ("08", "AABNK", "Natraj Kirana Pvt Ltd",            "FMCG distribution",    46311, (31.4, 58.7)),
    ("09", "AABAP", "Allahabad Produce Co Pvt Ltd",     "Agri-produce trading", 46203, (12.8, 24.1)),
    ("29", "AABMK", "Mysore Karpura Exports Pvt Ltd",   "Spice export",         10309, (17.6, 33.4)),
    ("33", "AABCR", "Chennai Retail Link Pvt Ltd",      "General trading",      46900, (28.4, 52.1)),
    ("20", "AABSR", "Santhal Resources Pvt Ltd",        "Mineral trading",      46710, (13.2, 26.7)),
    ("19", "AABKM", "Kolkata Merchants Pvt Ltd",        "Readymade garments",   46421, (21.8, 40.3)),
    ("23", "AABID", "Indore Distributors Pvt Ltd",      "Pharma wholesale",     46491, (34.7, 63.2)),
    ("32", "AABKC", "Kerala Cashew Corp Pvt Ltd",       "Cashew processing",    10801, (16.1, 30.7)),
]


def _clean_controls() -> list[dict]:
    out = []
    for i, (state, pan_pfx, name, sector, nic, (lo, hi)) in enumerate(_CLEAN_SECTOR_SPECS):
        # Generate a unique PAN
        pan = f"{pan_pfx}{random.randint(1000,9999)}{random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"
        gstin = make_gstin(state, pan)
        year = random.randint(2015, 2020)
        month = random.randint(1, 12)
        reg = f"{year}-{month:02d}-{random.randint(1,28):02d}"

        # 3 out of 15 controls have voluntarily cancelled GSTINs (legitimate)
        is_cancelled = i in (4, 9, 12)
        cancel_dt = f"{year + 3}-{month:02d}-01" if is_cancelled else None

        turnover_cr = random.uniform(lo, hi) + random.uniform(-1.8, 1.8)
        # Normal tax payment: ~18% of 60-80% of turnover (adjusted for exempt supplies)
        tax_paid = turnover_cr * random.uniform(0.55, 0.75) * 0.18 * 1e7
        tax_paid += random.uniform(-50000, 50000)  # small noise

        rec: dict[str, Any] = {
            "gstin": gstin,
            "name": name,
            "pan": pan,
            "sector": sector,
            "nic_code": nic,
            "registration_date": reg,
            "is_cancelled": is_cancelled,
            "taxpayer_type": "regular",
            "aggregate_turnover": round(turnover_cr * 1e7, 2),
            "tax_paid_ytd": round(max(tax_paid, 0), 2),
            "is_clean_control": True,
        }
        if cancel_dt:
            rec["cancellation_date"] = cancel_dt
            rec["voluntary_cancellation_reason"] = random.choice([
                "Business ceased — proprietor retired",
                "Merged into parent entity",
                "Converted to LLP",
            ])
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rings = {
        "ring.json": _demo_ring_7node(),
        "deccan_ring.json": _deccan_ring(),
        "delhi_triangle.json": _delhi_triangle(),
        "mumbai_spider.json": _mumbai_spider(),
        "cross_state_chain.json": _cross_state_chain(),
    }
    for fname, data in rings.items():
        path = OUT_DIR / fname
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        n_entities = len(data.get("gst_entities", []))
        n_edges = len(data.get("edges", []))
        print(f"  {fname}: {n_entities} entities, {n_edges} ITC edges")

    controls = _clean_controls()
    controls_path = OUT_DIR / "clean_controls.json"
    controls_path.write_text(
        json.dumps(controls, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  clean_controls.json: {len(controls)} clean-control companies")

    print(f"\nAll synthetic ITC data written to {OUT_DIR}")


if __name__ == "__main__":
    main()
