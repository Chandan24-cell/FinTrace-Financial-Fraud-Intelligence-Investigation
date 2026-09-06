# FinTrace — Financial Fraud Intelligence & Investigation

**One graph engine. Three structurally different fraud types.**
SME loans · GST ITC carousels · bank loan evergreening.

[![Live Frontend](https://img.shields.io/badge/frontend-VeriLens-brightgreen)](https://fin-trace-financial-fraud-intellige.vercel.app/dashboard)
[![API Backend](https://img.shields.io/badge/backend-Railway-blue)](https://fintrace-financial-fraud-intelligence-investigat-production.up.railway.app/docs)
[![Neo4j](https://img.shields.io/badge/database-Neo4j%205%20%2B%20GDS-blue)](https://neo4j.com)
[![License](https://img.shields.io/badge/data-CC--BY%20Gov.%20Sources-lightgrey)]()
[![GitHub](https://img.shields.io/badge/repo-Chandan24--cell-green)](https://github.com/Chandan24-cell/FinTrace-Financial-Fraud-Intelligence-Investigation)

---

<details open>
<summary><strong>📑 Table of Contents</strong> (tap a section — jumps straight there)</summary>

- [Problem & Domain](#problem--domain)
- [Objective](#objective)
- [Repository](#repository)
- [Approach](#approach)
- [Tech Stack](#tech-stack)
- [Sponsored Track — Neo4j](#sponsored-track)
- [Key Features](#key-features)
- [Deliverables](#deliverables)
- [Data Lineage](#data-lineage--available-sources-and-evidence)
- [What's Honestly Not Live](#whats-honestly-not-live-in-this-deployment)
- [Honest Framing of the DGGI ITC Ring Fixtures](#honest-framing-of-the-dggi-itc-ring-fixtures)
- [How to Run the Project](#how-to-run-the-project)
- [Production path](#production-path)
- [Future Scope](#future-scope)
- [Resources / Credits](#resources--credits)
- [Final Words](#final-words)

</details>

---

## Problem & Domain

Indian banks reported **₹33,148 crore** in loan-related bank fraud in FY25 — a **229% year-on-year surge** (RBI Annual Report 2024-25; loan-linked frauds rose from ₹10,072 cr in FY24, with public-sector banks accounting for over 71% of total fraud value). The same year, GST authorities detected **₹61,545 crore** in fake input-tax-credit (ITC) fraud across **25,009 fake firms** (Ministry of Finance, April 2025).

Listed companies have SEBI oversight; SMEs file once a year, often late, often rubber-stamped. Existing credit teams cannot cross-reference MCA21, CERSAI, GSTN, and the director-company ownership graph simultaneously.

**FinTrace does.** It produces a calibrated fraud risk score with a conformal prediction interval, a DataConfidence percentage, and a **typed evidence provenance chain** rooted in the graph as a traceable investigation artifact.

**Themes Selected:** Trust, Identity & Security · Work, Finance & Digital Economy · Public Systems, Governance and Civic Tech

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Objective

| Persona | Pain Point | What FinTrace Gives Them |
|---|---|---|
| **Credit review team** | Reviews SME applications manually and can miss fabricated P&Ls. | Calibrated fraud risk score + DataConfidence % + graph evidence chain. |
| **GST investigation team** | Identifies circular trading networks via spreadsheet cross-referencing. | GDS SCC highlights ITC carousel topology in the available graph data. |
| **Forensic review team** | Works from incomplete records after a suspected incident. | Multi-signal investigation report with typed evidence provenance. |

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Repository

[github.com/Chandan24-cell/FinTrace-Financial-Fraud-Intelligence-Investigation](https://github.com/Chandan24-cell/FinTrace-Financial-Fraud-Intelligence-Investigation)

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Approach

**Two-tier intelligence.** 12 deterministic rule modules (M0–M11) feed a 4-detector ML ensemble. The rules catch what they were designed for, the ML learns the optimal combination, and the anomaly detectors catch what neither knows about.
> The PRD specified 6 detectors; D1/D2 were dropped as redundant — see `docs/SYSTEM_DESIGN.md §5.5` for the sufficiency analysis.

**Graph-native explainability.** No SHAP, no LLM-generated numbers. Every fraud flag is a `FraudSignal` node with `TRIGGERED_BY` edges to the exact data point that triggered it. Designed for transparent investigation with traceable graph evidence.

**Calibration first.** Isotonic regression on a separate hold-out. Split-conformal prediction gives prediction intervals at α = 0.10 (a hand-rolled implementation; MAPIE was deferred due to API instability — same 90% coverage guarantee). We report uncertainty, not just a number.

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Tech Stack

**Frontend:** React 18 + Vite, TailwindCSS, d3-force, recharts, @tanstack/react-query

**Backend:** FastAPI + uvicorn (async), Pydantic v2

**Database:** Neo4j 5 Community + Graph Data Science (GDS) plugin — single data store

**ML:** LightGBM (OOF meta-learner), scikit-learn (Isolation Forest, LOF, Isotonic), PyTorch Geometric (TGN), Mamba SSM (TCN fallback), split-conformal intervals, NetworkX

**NLP / Docs:** spaCy, pdfplumber, camelot, pytesseract, reportlab

**AI reasoning:** GonkaRouter claim verification receives the supplied claim text and returns a structured verdict, rationale, confidence, evidence, and caveats. It does not independently retrieve external evidence.

**Data integrations:** MCA21/CERSAI where configured, BSE SME, NCLT/RBI fixtures and scrapers, and data.gov.in MCA bulk (CC-BY)

**Hosting:** Vercel (frontend) · Railway (FastAPI backend) · GonkaRouter (AI reasoning)

**Additional:** AI/ML · Cyber Security (financial fraud forensics) · Cloud

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Sponsored Track

**Neo4j Track** — Neo4j is the single data store. Every fraud signal, every audit log, every evidence chain lives in the graph.

- GDS **SCC** powers ITC carousel detection
- GDS **WCC** powers entity resolution
- Belief propagation Cypher writes `CONNECTED_TO_CRITICAL` edges

Local development uses Neo4j 5.20 Community + GDS 2.6.9. The Railway production environment does not provide that local graph stack, so graph-dependent features may use degraded fixture mode. Graph evidence provenance is the primary explainability mechanism in the application.

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Key Features

- **Three fraud types in one engine** — SME loan fraud, GST ITC carousels, bank loan evergreening
- **17 graph patterns + 12 deterministic modules (M0–M11) + 4 ML detectors** — all scores in < 2s/report
- **Calibrated P(fraud)** with 90% conformal prediction intervals — honest about uncertainty
- **Graph evidence provenance** — every finding cites specific numbers from a specific data row in the graph

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Deliverables

| Item | Link / Location |
|---|---|
| GitHub Repository | [FinTrace-Financial-Fraud-Intelligence-Investigation](https://github.com/Chandan24-cell/FinTrace-Financial-Fraud-Intelligence-Investigation) |
| Live Frontend | https://fin-trace-financial-fraud-intellige.vercel.app/dashboard |
| API Backend | https://fintrace-financial-fraud-intelligence-investigat-production.up.railway.app |
| API Docs | https://fintrace-financial-fraud-intelligence-investigat-production.up.railway.app/docs |
| Demo Data | Curated local fraud fixtures are available for repeatable demonstrations and QA |
| System Design | `docs/SYSTEM_DESIGN.md` — full technical reference (data sources, graph schema, rule modules, ML ensemble + sufficiency analysis, calibration, coverage matrix, personas, honest gaps) |
| Project Owner | Chandan Kumar Sah |
| Product | VeriLens |

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Data Lineage — Available Sources and Evidence

Fraud signals are grounded in the available fixture, uploaded, and public-record-derived data. The `/sources` endpoint shows the configured source inventory, refresh metadata, and record counts when available. Summary below:

| Source | Type | Records | Drives | Refresh |
|---|---|---|---|---|
| data.gov.in Tamil Nadu MCA bulk | Government CC-BY bulk | 191,531 companies | `/search` corpus | Quarterly (manual re-pull) |
| SFIO / CBI / NCLT confirmed-fraud labels | Court record | 14 famous cases | F1a meta-learner training | Manual (court records) |
| NCLT CP(IB) admitted proceedings | Court record | Real case numbers (e.g. `C.P.(IB) 4258/MB/2019`) | M9 override floor ≥ 75 | Auto via nclt-admitted scraper (planned) |
| RBI / CIBIL Wilful Defaulter list | Government scraper | Real declarations for IL&FS, DHFL, Amtek | M9 override | Weekly via `refresh-public-data.yml` (planned scraper) |
| DGGI press release archive | Government scraper | 5 ring topologies reconstructed from real busts | M4 patterns P08–P12 (ITC carousel) | Weekly via `.github/workflows/refresh-public-data.yml` |
| CERSAI charges register | Government scraper | Real charges for demo CINs | M4 P03, P14 | Manual |
| BSE SME platform disclosures | Industry benchmark | NIC sector averages | M5 peer deviation | Quarterly |
| 17 graph patterns (M4) | Real async Cypher + GDS | All 17 implemented | M4 module | n/a |
| 12 Tier-1 modules (M0–M11) | Real implementations | M0 master-data shell atlas + Beneish, Benford, peer dev, etc. | Tier-1 scoring | n/a |
| ML meta-learner (F1a/F1b/F1c) | LightGBM OOF + Isotonic + Split Conformal | Trained on the 14-case label set | `p_fraud_calibrated`, `p_fraud_interval` | Re-train on label update |

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## What's Honestly Not Live in This Deployment

| Item | Why | Workaround Used |
|---|---|---|
| MCA21 V3 live API | Paid subscription (~₹5–20k/mo) — out of hackathon budget | data.gov.in bulk covers TN; composite source falls through |
| GSTN live ITC feed | Restricted to licensed GSPs (₹25 lakh capital + MoU with GSTN) | Use DGGI press release archive — real bust topologies with amounts, zones, sectors |
| MCA Public Portal live scrape | Playwright + Chromium is not part of the hosted runtime | Local/operator use only — see `docs/INGEST_MCA_PUBLIC.md` |
| GonkaRouter claim verification | Requires `GONKA_API_KEY` on the backend | The endpoint reports unavailable when the provider is not configured; it analyzes only supplied claim text |

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Honest Framing of the DGGI ITC Ring Fixtures

The 5 files under `infra/seeds/itc_carousel/` are **not synthetic playground data**. Each file's `description`, `dggi_zone`, `total_fraud_cr`, `sector`, and `case_year` are drawn from publicly-reported DGGI Zonal Unit enforcement actions. Company names appear redacted because DGGI redacts them during active investigation — the `entity_disclosure` field on each ring file documents this.

The graph topology (which nodes form an SCC, which is the missing trader, which carries the high-ITC claim, where the director overlap sits) is preserved to drive Pattern P08–P12 detection on the demo. When the weekly CI cron pulls new DGGI press releases (see `.github/workflows/refresh-public-data.yml`), it appends `dggi_<slug>.json` files alongside the hand-curated five.

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## How to Run the Project

### Requirements

- Python 3.11 (managed via `uv`)
- Node.js 22
- Docker (for local Neo4j)
- Optional: NVIDIA GPU + CUDA 12+ for ML training (CPU works for inference)

### Local Setup

```bash
# Clone the repository
git clone https://github.com/Chandan24-cell/FinTrace-Financial-Fraud-Intelligence-Investigation.git
cd FinTrace-Financial-Fraud-Intelligence-Investigation

# 1. Python env
uv venv --python 3.11
.venv\Scripts\activate         # PowerShell: .venv\Scripts\Activate.ps1
uv pip install -e .[dev]

# 2. Neo4j 5 + GDS, locally
cp .env.example .env.local     # fill placeholders
docker compose -f infra/docker-compose.dev.yml up -d
python -m backend.app.graph.schema   # applies constraints + indexes

# 3. Backend
uvicorn backend.app.main:app --reload --port 8000

# 4. Frontend
cd frontend && npm install && npm run dev
```

Verify `RETURN gds.version()` works at [http://localhost:7474](http://localhost:7474) — that's the PRD Day 1 acceptance check.

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Production path

| Component | Target | How |
|---|---|---|
| Frontend | Vercel | Browser-facing application |
| FastAPI backend | Railway | API and analysis service |
| AI reasoning | GonkaRouter | Claim text verification when configured |
| Graph development stack | Local Docker | Neo4j 5.20 Community + GDS 2.6.9; not supplied by Railway |

Production graph-dependent features may operate in degraded fixture mode when the
local Neo4j/GDS development environment is unavailable.

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Future Scope

Items below are correctly deferred (PRD §15). **Not missing** — out of 30-day scope:

- GSP licence / live GST invoice data
- Consortium fraud signal sharing across NBFCs
- Continuous monitoring with daily score updates
- LOS integration (Finacle, BankFlex, Nucleus)
- LLP / partnership firm coverage
- Cross-border / offshore structure detection

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Resources / Credits

**Data sources:** MCA21 · CERSAI · BSE SME · NCLT · RBI Wilful Defaulter list · CIBIL public defaulter list

**Frameworks:** FastAPI, Neo4j GDS, PyTorch Geometric, LightGBM, scikit-learn

**Methodology references:**
- SFIO IL&FS forensic report (FY2014-18)
- Beneish, 1999 — *The Detection of Earnings Manipulation*
- Nigrini, 2012 — *Benford's Law*
- Vovk et al. — *Conformal Prediction*
- Rossi et al., 2020 — *Temporal Graph Networks*
- Gu & Dao, 2023 — *Mamba*

**Statistics:** RBI Annual Report 2024-25 (loan-fraud figures); Ministry of Finance / GST data, April 2025 (fake-ITC figures)

**Tooling:** Claude Code + `obra/superpowers` + `affaan-m/everything-claude-code`

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>

---

## Final Words

If time runs out in Week 4, cut the Upload Portal and Benchmark screen.

**Never cut:**
1. The IL&FS demo
2. The Graph Explorer with evidence provenance
3. The ITC carousel view
4. The DHFL evergreening view

Those four things are FinTrace. Everything else is polish.

<div align="right"><a href="#table-of-contents">⬆ Back to top</a></div>
