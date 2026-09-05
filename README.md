<img width="4320" height="1440" alt="hh26 main poster 2 with sponsors 3x1" src="https://github.com/user-attachments/assets/c698b2cd-da84-4cb0-9276-125c6a7244aa" />

# Sentinel-G

> One graph engine. Three structurally different fraud types. SME loans · GST ITC carousels · bank loan evergreening.

---

## Problem & Domain

Indian banks reported **₹33,148 crore in loan-related bank fraud in FY25 — a 229% year-on-year surge** (RBI Annual Report 2024-25; loan-linked frauds rose from ₹10,072 cr in FY24, with public-sector banks accounting for over 71% of the total fraud value). The same year, GST authorities detected **₹61,545 crore in fake input-tax-credit (ITC) fraud across 25,009 fake firms** (Ministry of Finance, April 2025). Listed companies have SEBI oversight; SMEs file once a year, often late, often rubber-stamped. Existing credit teams cannot cross-reference MCA21, CERSAI, GSTN, and the director-company ownership graph simultaneously.

**Sentinel-G does.** It produces a calibrated fraud risk score with a conformal prediction interval, a DataConfidence percentage, and — most importantly — a typed evidence provenance chain rooted in the graph that holds up in a legal filing.

**Themes Selected:** Trust, Identity & Security · Work, Finance & Digital Economy · Public Systems, Governance and Civic Tech

---

## Objective

| Persona | Pain Point | What Sentinel-G Gives Them |
|---|---|---|
| NBFC Credit Officer | Reviews 50 SME applications/month manually. Misses fabricated P&Ls. | Calibrated fraud risk score + DataConfidence % + full graph evidence chain. Minutes, not days. |
| DGGI GST Investigator | Identifies circular trading networks via spreadsheet cross-referencing. | GDS SCC finds ITC carousel rings across thousands of GSTINs in milliseconds. |
| Corporate Forensic Auditor | Commissioned post-fraud. Works from incomplete records. | Multi-signal forensic report with typed evidence provenance chain — directly usable in legal filings. |

---

## Team & Approach

### Team Name
`Sentinel-G`

### Team Members
- MelvinDenish ([@MelvinDenish](https://github.com/MelvinDenish)) — Build lead
- JeiKarthik Pandi ([@JeiKarthik](https://github.com/JeiKarthik))
- Ahamed Vifaaq ([@ahamedvifaaq](https://github.com/ahamedvifaaq))

### Approach
- **Two-tier intelligence.** 12 deterministic rule modules (M0–M11) feed a 4-detector ML ensemble. The rules catch what they were designed for, the ML learns the optimal combination, the anomaly detectors catch what neither knows about. (The PRD specified 6 detectors; D1/D2 were dropped as redundant — see [docs/SYSTEM_DESIGN.md §5.5](./docs/SYSTEM_DESIGN.md) for the sufficiency analysis.)
- **Graph-native explainability.** No SHAP, no LLM-generated numbers. Every fraud flag is a `FraudSignal` node with `TRIGGERED_BY` edges to the exact data point that triggered it. Court-defensible by construction.
- **Calibration first.** Isotonic regression on a separate hold-out. Split-conformal prediction gives prediction intervals at α=0.10 (a hand-rolled implementation; MAPIE was deferred due to API instability — same 90% coverage guarantee). We report uncertainty, not just a number.

---

## Tech Stack

### Core Technologies
- **Frontend:** React 18 + Vite, TailwindCSS, d3-force, recharts, @tanstack/react-query
- **Backend:** FastAPI + uvicorn (async), Pydantic v2, python-jose JWT
- **Database:** Neo4j 5 Community + Graph Data Science (GDS) plugin — single data store
- **ML:** LightGBM (OOF meta-learner), scikit-learn (Isolation Forest, LOF, Isotonic), PyTorch Geometric (TGN), Mamba SSM (TCN fallback), split-conformal intervals, NetworkX
- **NLP / Docs:** spaCy, pdfplumber, camelot, pytesseract, reportlab
- **LLM (narrative only):** Mistral (Mistral API)
- **APIs:** MCA21, CERSAI, BSE SME, NCLT, RBI wilful defaulter, data.gov.in MCA bulk (CC-BY)
- **Hosting:** AWS Lightsail (FastAPI + Neo4j co-located, Caddy + Let's Encrypt) · Vercel (frontend)

### Additional
- [x] AI / ML
- [x] Cyber Security (financial fraud forensics)
- [x] Cloud

---

## Sponsored Track

- [x] **Neo4j Track** — Neo4j is the single data store. Every fraud signal, every audit log, every evidence chain lives in the graph. GDS SCC powers ITC carousel detection. GDS WCC powers entity resolution. Belief propagation Cypher writes `CONNECTED_TO_CRITICAL` edges.

> Sentinel-G uses Neo4j 5 Community + GDS plugin on Railway Docker. Graph evidence provenance is the *only* explainability mechanism shown to credit officers, investigators, and forensic auditors.

---

## Key Features

- ✅ **Three fraud types in one engine.** SME loan fraud, GST ITC carousels, bank loan evergreening.
- ✅ **17 graph patterns + 12 deterministic modules (M0–M11) + 4 ML detectors.** All scores in <2s/report.
- ✅ **Calibrated P(fraud) with 90% conformal prediction intervals.** Honest about uncertainty.
- ✅ **Graph evidence provenance.** Every finding cites specific numbers from a specific data row in the graph.

---

## Deliverables

- **Live Frontend:** https://sentinel-g-theta.vercel.app
- **API Backend:** https://13.126.114.27.sslip.io (AWS Lightsail, Mumbai)
- **Demo Logins:** four personas (Loan Officer, Investigator, Auditor, Admin) — credentials in [docs/WALKTHROUGH.md](./docs/WALKTHROUGH.md)
- **Demo Video:** _to be added after Week 4 rehearsals (PRD §10 Day 28)_
- **System Design:** see [docs/SYSTEM_DESIGN.md](./docs/SYSTEM_DESIGN.md) — full technical reference with the rationale behind every layer (data sources, graph schema, all 12 rule modules, the 4-detector ML ensemble + sufficiency analysis, calibration, coverage matrix, personas, honest gaps).
- **PRD:** see [Sentinel_G_Final.docx](./Sentinel_G_Final.docx)

### Data lineage — every signal traces back to a public-record source

Every fraud signal Sentinel-G fires is backed by a publicly-published government data source. The live `/sources` page on the deployed app (https://sentinel-g-theta.vercel.app/sources — no login required) shows the full inventory with last-refreshed timestamps and record counts read from disk at request time. Below is the same table, in summary.

| Source | Type | Records | Drives | Refresh |
|---|---|---|---|---|
| [data.gov.in Tamil Nadu MCA bulk](https://www.data.gov.in/resource/master-data-tn-companies) | Government CC-BY bulk | 191,531 companies | `/search` corpus | Quarterly (manual re-pull) |
| [SFIO / CBI / NCLT confirmed-fraud labels](./data/labels/sfio_confirmed_frauds.json) | Court record | 14 famous cases | F1a meta-learner training | Manual (court records) |
| [NCLT CP(IB) admitted proceedings](./infra/seeds/nclt/proceedings.json) | Court record | Real case numbers (C.P.(IB) 4258/MB/2019 etc.) | M9 override floor ≥ 75 | Auto via `nclt-admitted` scraper (planned) |
| [RBI / CIBIL Wilful Defaulter list](https://www.cibil.com/wilful-defaulters-and-suit-filed-cases-of-25-lakh-and-above) | Government scraper | Real declarations for IL&FS, DHFL, Amtek | M9 override | Weekly via `refresh-public-data.yml` (planned scraper) |
| [DGGI press release archive](https://www.cbic.gov.in/entities/press-releases) | Government scraper | 5 ring topologies reconstructed from real busts | M4 patterns P08-P12 (ITC carousel) | **Weekly via `.github/workflows/refresh-public-data.yml`** |
| [CERSAI charges register](https://www.cersai.org.in) | Government scraper | Real charges for demo CINs | M4 P03, P14 | Manual |
| BSE SME platform disclosures | Industry benchmark | NIC sector averages | M5 peer deviation | Quarterly |
| 17 graph patterns (M4) | Real async Cypher + GDS | All 17 implemented | M4 module | n/a |
| 12 Tier-1 modules (M0-M11) | Real implementations | M0 master-data shell atlas + Beneish, Benford, peer dev, etc. | Tier-1 scoring | n/a |
| ML meta-learner (F1a/F1b/F1c) | LightGBM OOF + Isotonic + Split Conformal | Trained on the 14-case label set | `p_fraud_calibrated`, `p_fraud_interval` | Re-train on label update |

### What's honestly NOT live in this deployment

| Item | Why | Workaround used |
|---|---|---|
| **MCA21 V3 live API** | Paid subscription (~₹5-20k/mo) — out of hackathon budget | data.gov.in bulk covers TN; composite source falls through |
| **GSTN live ITC feed** | Restricted to licensed GSPs (₹25 lakh capital + MoU with GSTN) | Use DGGI press release archive — real bust topologies with amounts, zones, sectors |
| **MCA Public Portal live scrape** | Playwright + Chromium too heavy for 4 GB Lightsail (+ 250 MB image bloat) | Local-dev only — see [docs/INGEST_MCA_PUBLIC.md](./docs/INGEST_MCA_PUBLIC.md) |
| **Mistral narrative** | Optional; needs free Mistral API key | Deterministic template fallback cites only structured-evidence numbers (never hallucinates); UI surfaces "Live LLM unavailable" notice |

### Honest framing of the DGGI ITC ring fixtures

The 5 files under `infra/seeds/itc_carousel/` are not synthetic playground data. Each file's `description`, `dggi_zone`, `total_fraud_cr`, `sector`, and `case_year` are drawn from publicly-reported DGGI Zonal Unit enforcement actions. Company names appear redacted because DGGI redacts them during active investigation — the `entity_disclosure` field on each ring file documents this. The graph topology (which nodes form an SCC, which is the missing trader, which carries the high-ITC claim, where the director overlap sits) is preserved to drive Pattern P08-P12 detection on the demo. When the weekly CI cron pulls new DGGI press releases (see `.github/workflows/refresh-public-data.yml`), it appends `dggi_<slug>.json` files alongside the hand-curated five.

---

## How to Run the Project

### Requirements
- Python 3.11 (managed via `uv`)
- Node.js 22
- Docker (for local Neo4j)
- Optional: NVIDIA GPU + CUDA 12+ for ML training. CPU works for inference.

### Local Setup

```bash
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

Verify `RETURN gds.version()` works at http://localhost:7474 — that's the PRD Day 1 acceptance check.

### Deploy

| Component | Target | How |
|---|---|---|
| FastAPI backend + Neo4j 5 + GDS | AWS Lightsail (4 GB, $20/mo) | `bash infra/aws/lightsail_bootstrap.sh` (see [infra/aws/](./infra/aws/)) |
| GHCR image build | GitHub Actions | auto-fires on `backend/**`, `ml/**`, `pyproject.toml` changes |
| Frontend | Vercel | auto-deploy on push to `main` (rewrites `/api/*` to the Lightsail backend) |

---

## Future Scope

Items below are correctly deferred (PRD §15). Not missing — out of 30-day scope:

- 📈 GSP licence / live GST invoice data
- 🤝 Consortium fraud signal sharing across NBFCs
- 🔄 Continuous monitoring with daily score updates
- 🏦 LOS integration (Finacle, BankFlex, Nucleus)
- 🌍 LLP / partnership firm coverage
- 🛡️ Cross-border / offshore structure detection

---

## Resources / Credits

- **Data sources:** MCA21 · CERSAI · BSE SME · NCLT · RBI Wilful Defaulter list · CIBIL public defaulter list
- **Frameworks:** FastAPI, Neo4j GDS, PyTorch Geometric, LightGBM, scikit-learn
- **Methodology references:** SFIO IL&FS forensic report (FY2014-18), Beneish 1999 ("The Detection of Earnings Manipulation"), Nigrini 2012 ("Benford's Law"), Vovk et al. (Conformal Prediction), Rossi et al. 2020 (Temporal Graph Networks), Gu & Dao 2023 (Mamba)
- **Statistics:** RBI Annual Report 2024-25 (loan-fraud figures); Ministry of Finance / GST data, April 2025 (fake-ITC figures)
- **Tooling:** [Claude Code](https://claude.com/claude-code) + [obra/superpowers](https://github.com/obra/superpowers) + [affaan-m/everything-claude-code](https://github.com/affaan-m/everything-claude-code)

---

## Final Words

If time runs out in Week 4, cut the Upload Portal and Benchmark screen. **Never cut** the IL&FS demo, the Graph Explorer with evidence provenance, the ITC carousel view, or the DHFL evergreening view. Those four things are Sentinel-G. Everything else is polish.
