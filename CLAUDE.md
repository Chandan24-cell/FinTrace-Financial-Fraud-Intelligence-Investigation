# Sentinel-G — Claude Operating Instructions

> SME Financial Fraud Detection Platform · HackHazards '26 · 30-day build · PRD v4.0 (final, frozen)

This file is the authoritative working context. The full requirements live in [Sentinel_G_Final.docx](./Sentinel_G_Final.docx). Treat that PRD as **frozen** — do not deviate from it without an explicit user override.

---

## 1. What this project is

- **Track:** Neo4j / AuraDB (running on Railway Docker — see §3)
- **Three fraud types detected:** SME loan fraud · GST ITC Carousel · Bank loan Evergreening
- **Stack:** FastAPI · Neo4j 5 (Community + GDS) · LightGBM · PyTorch Geometric · Mamba SSM · React+Vite · Gemini Flash (narrative only)
- **Day 1 = 2026-05-12. Demo deadline = 2026-06-11 (Day 30).**

## 2. Non-negotiable architecture constraints

These were debated and decided in PRD §2.1. **Do not reintroduce any of these**:

| ❌ DO NOT | ✅ INSTEAD |
|---|---|
| PostgreSQL / Supabase / SQLAlchemy / Alembic | Neo4j is the single data store. Audit logs = `DataQualityError` nodes. |
| Redis / Upstash | Cache as Neo4j node properties (`last_scored_at`). |
| Celery / RQ / dramatiq | `asyncio.gather()` for parallelism. |
| Expo mobile app | Web dashboard only (React + Vite). |
| XBRL parsers | pdfplumber + camelot for PDF parsing. |
| SHAP / TreeExplainer (user-facing) | Graph-native evidence provenance via `FraudSignal` nodes + `TRIGGERED_BY` edges. SHAP is allowed *only* internally for LightGBM meta-learner debugging — never surface it. |
| Llama 3 / llama-cpp / transformers for narrative | Gemini Flash (Google AI Studio free tier, 60 req/min). |
| LLM-generated numbers | Numbers come from the graph. LLM gets a structured evidence JSON + template — it writes prose only. |
| Static formula `S = 0.4·GNN + 0.2·VAE + …` | Fully deprecated. F1a LightGBM OOF meta-learner handles all aggregation. Grep for the old formula at the end and confirm zero hits (PRD Definition of Done line). |

## 3. Decided this session (binding)

| Question | Decision | Why |
|---|---|---|
| Neo4j hosting | Railway Docker `neo4j:5-community` + GDS plugin | PRD verbatim. AuraDB Free has no GDS. |
| Python version | **3.11** managed via `uv` | 3.14 (system default) has no PyTorch / torch-geometric / mamba-ssm wheels. |
| GPU | RTX 3050 6GB local CUDA 13.2 — Mamba can be trained locally | Fly.io free is CPU-only so production loads pre-trained weights. |
| PRD revisions | **None.** PRD v4.0 accepted verbatim including TGN on 160 labels, AUC ≥ 0.96 target, real MCA21/CERSAI scrapers. | User explicitly overrode my 6 concerns. Build exactly what is written. |
| Plugin install | `obra/superpowers` + `affaan-m/everything-claude-code (ecc)` at user scope | Repo stays clean — only `CLAUDE.md`, `.claude/settings.json`, `.mcp.json` are committed. |
| MCPs enabled | github, context7, sequential-thinking, memory, playwright | See `.mcp.json`. |
| API keys | All placeholders in `.env.example` for now | User will fill `.env.local` later. |

## 4. Repo layout (target)

```
SME_Fraud_Detection/
├── backend/                     # FastAPI app — runs on Fly.io
│   ├── app/
│   │   ├── main.py              # FastAPI entrypoint
│   │   ├── config.py            # pydantic-settings
│   │   ├── deps.py              # Neo4j driver, auth, etc.
│   │   ├── auth/                # python-jose JWT (PRD §10 Day 2)
│   │   ├── api/                 # routers: /analyse, /upload, /search, /report
│   │   ├── ingest/              # MCA21, CERSAI, BSE SME, NCLT, wilful defaulter scrapers
│   │   ├── parse/               # pdfplumber + camelot + pytesseract + PDF forensics
│   │   ├── graph/               # Neo4j Cypher: schema, writes, traversals
│   │   ├── modules/             # Tier-1 rules (11 modules from PRD §4)
│   │   │   ├── m01_beneish.py
│   │   │   ├── m02_cross_statement.py
│   │   │   ├── m03_benford.py
│   │   │   ├── m04_graph_patterns.py        # all 17 patterns
│   │   │   ├── m05_peer_deviation.py
│   │   │   ├── m06_temporal.py
│   │   │   ├── m07_auditor_nlp.py
│   │   │   ├── m08_document_forensics.py
│   │   │   ├── m09_nclt_defaulter.py
│   │   │   ├── m10_hypergraph_shell.py
│   │   │   └── m11_anomaly.py               # IsoForest + LOF
│   │   ├── scorer.py            # RiskScorer — orchestrates 11 modules via asyncio.gather
│   │   ├── provenance.py        # Cypher traversal for evidence chain
│   │   ├── narrative.py         # Gemini Flash prompt + template substitution
│   │   └── report.py            # reportlab PDF with UUID + timestamp
│   ├── tests/
│   ├── Dockerfile
│   └── fly.toml
├── ml/                          # ML engine — Phase 1–4 from PRD §9
│   ├── l05_graph_features.py    # NetworkX 7-feature pipeline (PRD §5.3)
│   ├── detectors/
│   │   ├── d3_tgn.py            # PyG TGNMemory + GraphAttentionEmbedding
│   │   ├── d4_lof.py
│   │   ├── d5_mamba.py          # mamba-ssm with TCN fallback
│   │   └── d6_combined_ae.py    # D1 (CatBoost) + D2 (β-VAE) removed 2026-05-21 — both were 1-line docstring stubs; revisit if real training data lands. See LOCAL_TEST_REPORT §3.1.
│   ├── meta/
│   │   ├── f1a_lightgbm_oof.py  # ← loaded by backend/app/ml_inference.py at runtime
│   │   ├── f1b_isotonic.py      # ← loaded by backend/app/ml_inference.py at runtime
│   │   └── f1c_split_conformal.py  # ← loaded by backend/app/ml_inference.py at runtime (renamed 2026-05-22 from f1c_mapie.py — module name now matches the split-conformal implementation; MAPIE deferred)
│   ├── explain/
│   │   └── gnn_explainer.py     # for TGN subgraph viz only
│   ├── training/                # Colab-ready notebooks for CUDA training
│   ├── artifacts/               # saved weights / calibration / conformal
│   └── tests/
├── frontend/                    # React + Vite — Vercel
│   ├── src/
│   │   ├── pages/               # Search, Dashboard, GraphExplorer, ITCCarousel, Evergreening, Upload, Reports
│   │   ├── components/
│   │   ├── lib/                 # API client, react-query hooks
│   │   └── viz/                 # d3-force, recharts
│   ├── package.json
│   └── vite.config.ts
├── infra/
│   ├── docker-compose.dev.yml   # local Neo4j 5 + GDS for dev
│   ├── railway/                 # Railway service config
│   └── seeds/                   # IL&FS, DHFL, ITC ring static JSON (PRD §10 Day 6)
├── data/
│   ├── raw/                     # gitignored
│   ├── interim/                 # gitignored
│   ├── processed/               # gitignored
│   └── labels/                  # SFIO 14 confirmed fraud cases — PRD §10 Day 12
├── scripts/
│   ├── seed_neo4j.py            # repopulate full demo seed <60s (PRD Definition of Done)
│   ├── precache_companies.py    # MCA + CERSAI pre-cache run (PRD Day 7, 17)
│   └── pdf_test_extract.py
├── tests/                       # cross-cutting integration tests
├── .claude/
│   └── settings.json            # project permissions
├── .mcp.json                    # 5 MCP servers
├── CLAUDE.md                    # this file
├── README.md                    # judge-facing
├── pyproject.toml               # uv + ML + backend deps
├── .python-version              # 3.11
└── .env.example
```

## 5. Module → Owner map (so Claude knows what file fires what)

Single source of truth for which file implements which PRD module. **Before adding a new file, check this table.**

| PRD Section | Module | File |
|---|---|---|
| §4.1 | M1 Beneish M-Score (8 ratios) | `backend/app/modules/m01_beneish.py` |
| §4.2 | M2 Cross-Statement (7 checks) | `backend/app/modules/m02_cross_statement.py` |
| §4.3 | M3 Benford's Law | `backend/app/modules/m03_benford.py` |
| §4.4 | M4 Graph patterns (17 patterns: 7 SME + 5 ITC + 5 Evergreening) | `backend/app/modules/m04_graph_patterns.py` |
| §4.5 | M5 Peer deviation | `backend/app/modules/m05_peer_deviation.py` |
| §4.6 | M6 Temporal signals (8) | `backend/app/modules/m06_temporal.py` |
| §4.7 | M7 Auditor NLP (spaCy) | `backend/app/modules/m07_auditor_nlp.py` |
| §4.8 | M8 Document forensics | `backend/app/modules/m08_document_forensics.py` |
| §4.9 | M9 NCLT / DRT / Wilful Defaulter | `backend/app/modules/m09_nclt_defaulter.py` |
| §4.10 | M10 Hypergraph shell detection | `backend/app/modules/m10_hypergraph_shell.py` |
| §4.11 | M11 Anomaly (IsoForest + LOF) | `backend/app/modules/m11_anomaly.py` |
| §5.1 D3–D6 | ML detectors | `ml/detectors/d3_tgn.py`, `d4_lof.py`, `d5_mamba.py`, `d6_combined_ae.py` (D1 CatBoost + D2 β-VAE removed 2026-05-21 — were unimplemented stubs) |
| §5.2 inference bridge | Meta-learner loader (F1a→F1b→F1c) called from RiskScorer | `backend/app/ml_inference.py` — wired 2026-05-21 to populate `p_fraud_calibrated` + `p_fraud_interval` |
| §5.2 F1a/b/c | Meta-learner stack | `ml/meta/f1a_lightgbm_oof.py`, `f1b_isotonic.py`, `f1c_split_conformal.py` |
| §5.3 | L0.5 graph feature extraction | `ml/l05_graph_features.py` |
| §5.4 | GNNExplainer | `ml/explain/gnn_explainer.py` |
| §5.5 | Gemini Flash narrative | `backend/app/narrative.py` |
| §6 | Graph evidence provenance | `backend/app/provenance.py` |
| §7.1–7.4 | RiskScorer dual output + override rules | `backend/app/scorer.py` |

## 6. Graph schema (frozen — PRD §3)

Node labels (core): `Company`, `Director`, `FinancialStatement`, `FraudSignal`, `DataQualityError`, `SharedAttribute`, `IndustryBenchmark`, `NCLTProceeding`, `WilfulDefaulter`.

Fraud extension labels: `GSTEntity`, `ITCClaim`, `MissingTrader`, `LoanDisbursement`, `LoanRepayment`, `EvergreeningCluster`.

Relationship types: `IS_DIRECTOR_OF`, `IS_SHAREHOLDER_OF`, `HAS_COMMON_DIRECTOR`, `TRANSACTS_WITH`, `HAS_FINANCIALS`, `HAS_FRAUD_SIGNAL`, `TRIGGERED_BY`, `SHARES_ATTRIBUTE`, `CONNECTED_TO_CRITICAL`, `HAS_NCLT_PROCEEDING`, `CLAIMS_ITC_FROM`, `HAS_GST_ENTITY`, `IS_MISSING_TRADER`, `IN_CAROUSEL_RING`, `RECEIVED_LOAN`, `REPAID_LOAN`, `FUNDED_REPAYMENT_OF`, `IN_EVERGREENING_CLUSTER`.

**Every fraud flag = a `FraudSignal` node with `TRIGGERED_BY` edges to the exact `RelatedPartyTransaction` / `FinancialStatement` / `Charge` / `GSTEntity` / `LoanDisbursement` that caused it.** Evidence strings always cite specific numbers. Never `"revenue appears inflated"`. Always `"Revenue per P&L (₹12.4 cr) exceeds GST taxable turnover (₹8.2 cr) by 51.2% — above 5% tolerance."` (PRD §4.2 verbatim rule.)

## 7. Two-score output (PRD §7.1) — every analysis returns BOTH

```python
{
  "fraud_risk_score": 0–100,                  # Tier-1 weighted aggregate
  "risk_band": "LOW|MEDIUM|HIGH|CRITICAL",
  "p_fraud_calibrated": 0.0–1.0,              # F1b isotonic
  "p_fraud_interval": [low, high],            # F1c conformal, alpha=0.10
  "data_confidence": 0–100,                   # DataCompletenessScore
  "ensemble_disagreement_flag": bool,         # True when any 2 L1 detector scores diverge >30 pts
  "evidence_chain": [FraudSignal...]          # with TRIGGERED_BY provenance
}
```

**Never** output `fraud_risk_score` without `data_confidence`. **Never** output `data_confidence` without `fraud_risk_score`. Override rules (§7.3): any CRITICAL flag forces `fraud_risk_score ≥ 60`. NCLT/wilful-defaulter match forces `fraud_risk_score ≥ 75`.

## 8. 30-day plan checkpoints (PRD §10)

| Week | Days | Goal |
|---|---|---|
| 1 | 1–7 | Monorepo, FastAPI, Neo4j schema, MCA/CERSAI scrapers, BSE benchmarks, NCLT scraper, IL&FS+DHFL+ITC seed, 200-co pre-cache |
| 2 | 8–14 | All 11 rule modules. All 17 graph patterns. 6 L1 detectors. OOF retrain. Belief propagation. SFIO labels. |
| 3 | 15–21 | RiskScorer orchestration. Upload endpoints. 1000-co pre-cache. React Dashboard + Graph Explorer + ITC view + Evergreening view + Upload + Reports. |
| 4 | 22–30 | Benchmark, conformal audit, stress test, security lockdown, 3 rehearsals, deploy to prod, demo video, Devfolio submission. |

ML phases 1–4 (PRD §9) run **in parallel** — phases map to weeks.

**Definition of Done** is PRD §13. Twenty-four checkboxes. Every box ticked = product is shippable. Demo never crashes (3 successful rehearsals).

## 9. Demo (PRD §14) — 3 minutes, 3 fraud types

| Time | Screen | Story |
|---|---|---|
| 0:00–0:20 | Company Search | IL&FS CIN: `U45201MH2005PTC155294` FY 2016-17 |
| 0:20–0:50 | Analysis Dashboard | CRITICAL score + calibrated P(fraud) + conformal interval + DataConfidence |
| 0:50–1:15 | Graph Explorer | Multi-hop director chain |
| 1:15–1:40 | Evidence Provenance | FraudSignal → TRIGGERED_BY chain |
| 1:40–2:00 | ITC Carousel View | 7-node SYNTHETIC DATA ring |
| 2:00–2:20 | Evergreening View | DHFL — patterns 13/14/15 fire simultaneously |
| 2:20–3:00 | Report Export | PDF with UUID + timestamp + disclaimer |

**Never cut from demo:** IL&FS · Graph Explorer with evidence provenance · ITC carousel view · DHFL evergreening view. Everything else is polish.

## 10. House rules for Claude (development practices)

- **Frozen PRD.** Treat PRD v4.0 numbers as canon. If implementation reveals an issue, raise it — do not silently change scope.
- **Specific evidence strings only.** Every `FraudSignal.evidence_string` must contain exact numbers from the source data. No generic text.
- **uv for Python.** All `pip install` goes through `uv add` / `uv pip install`. Lockfile is `uv.lock`. Never install into system Python 3.14.
- **Async-first.** Backend handlers `async def`. Neo4j driver: `AsyncGraphDatabase.driver`. Parallelism: `asyncio.gather`. Never thread pools, never Celery.
- **No secrets in code.** `.env.local` is gitignored. `.env.example` carries placeholders. CI runs `git grep` for known secret patterns before deploy.
- **Tests at write-time.** When implementing module M_x, write the test for M_x first using the IL&FS seed data. PRD §13 Definition of Done requires pattern tests pass.
- **Stop and ask** if the task requires deviating from PRD v4.0 or from a decision in §3 of this file.

## 11. Tool / skill quick-reference

- **everything-claude-code (ecc)** is installed — 220 skills, 58 agents. Useful subagents for this project: `planner`, `python-reviewer`, `code-reviewer`, `tdd-guide`, `pytorch-build-resolver`, `mle-reviewer`, `security-review`.
- **obra/superpowers** is installed — TDD workflow, brainstorming, writing-plans, using-git-worktrees, verification-loop. Use these *as skills*, not as a substitute for judgment.
- **MCP servers** (auto-loaded from `.mcp.json` on project trust): github, context7, sequential-thinking, memory, playwright.
  - `context7` is the right tool when you need fresh docs for FastAPI / Neo4j / torch-geometric / lightgbm / mamba-ssm — do not guess API signatures.
  - `playwright` is the right tool for testing the React UI in a real browser, and for the MCA21/CERSAI scrapers (handles captchas and JS-heavy aspx pages).

## 12. Reference

- [Sentinel_G_Final.docx](./Sentinel_G_Final.docx) — full PRD v4.0
- [README.md](./README.md) — judge-facing project description (populate per Devfolio template)
- [.env.example](./.env.example) — env var contract
