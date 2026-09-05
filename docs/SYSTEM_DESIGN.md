# Sentinel-G — System Design Document (SDD)

> The single technical reference for how Sentinel-G turns raw public records into a
> court-defensible fraud finding. It documents **what is actually built**, and — for
> every layer — **why that design was chosen over the alternatives**.
>
> Status: reflects the deployed system as of 2026-06-19. Where the original PRD v4.0
> (`Sentinel_G_Final.docx`) specified something we deliberately did not ship, this
> document says so explicitly and explains the engineering reason (see §10).

---

## 1. Document Purpose & Scope

This document is the end-to-end technical reference for Sentinel-G: from the raw
government data that enters the system, through the graph that stores it, the
two-tier detection engine that scores it, the calibration layer that makes the
scores honest, and the role-specific outputs that consumers act on. **Every section
states not just *what* a component does but *why* it was chosen** — the trade-off
that was weighed and the alternative that was rejected.

**What this document covers.** Data provenance and source selection; the Neo4j graph
schema; all 12 rule modules (M0–M11); the live 4-detector ML ensemble and its
meta-learner; calibration and conformal uncertainty; a behaviour×detector coverage
matrix; the output layer and evidence provenance; the persona→input→output journey;
and an honest register of what is not yet live, with effort estimates.

**What this document is *not*.** It is not a setup or deployment guide
(see [RUNNING.md](./RUNNING.md), [DEPLOY_AWS_LIGHTSAIL.md](./DEPLOY_AWS_LIGHTSAIL.md)).
It is not a product requirements document (that is the frozen
[Sentinel_G_Final.docx](../Sentinel_G_Final.docx), PRD v4.0). It is not API reference
documentation (that is the live OpenAPI at `/docs`).

**Intended reader.** Technical hackathon judges evaluating methodology; contributors
who need to understand a layer before changing it; and forensic / financial-crime
domain experts auditing whether the detection approach is sound and the evidence
chain is defensible.

**A note on honesty.** Sentinel-G was built against a frozen PRD that specified six ML
detectors and the MAPIE conformal library. Two detectors (D1 CatBoost, D2 β-VAE) were
removed during the build as unimplemented stubs, and MAPIE was replaced with a
hand-rolled split-conformal implementation. This document describes the system **as it
actually runs**, and §5.5 + §10 explain — with a sufficiency analysis — why the
4-detector engine is not missing coverage, only deferring redundant capacity until the
labelled-data regime justifies it.

---

## 2. Data Sources

Sentinel-G's central design principle is **every fraud signal must trace to a
publicly-published government record.** A score a credit committee or a court cannot
audit back to source is worthless. The live `/sources` endpoint
([backend/app/api/sources.py](../backend/app/api/sources.py)) is the public,
no-auth "judge-defense surface" that lists every source with its originating URL,
licence, on-disk last-refreshed mtime, and record count read at request time.

For each source below: what it is, how it's accessed, what it contributes, what it
feeds, its limitations, and **why it was chosen over the alternative.**

### 2.1 data.gov.in — Tamil Nadu MCA company master (bulk)
- **What / publisher:** Ministry of Corporate Affairs company master data, republished
  as a CC-BY bulk dataset on data.gov.in.
- **Access:** Bulk download (manual quarterly re-pull). Parsed by
  [backend/app/ingest/data_gov_in.py](../backend/app/ingest/data_gov_in.py).
- **Fields/entities:** CIN, company name, NIC industry code, state, incorporation date,
  authorised/paid-up capital, registered address, status. **191,531 companies.**
- **Feeds:** the `/search` corpus and **M0 master-data shell atlas** (address clusters,
  mass-incorporation events, paper-shell capital ratios).
- **Limitations:** Tamil Nadu only; master-data only (no financials, no directors, no
  charges) — which is precisely why M0 exists, to extract signal from master data alone.
- **Why this over the alternative (MCA21 V3 live API):** the MCA21 V3 API is a paid
  subscription (~₹5–20k/mo), out of hackathon budget. The CC-BY bulk gives a real,
  large, legally-redistributable corpus with zero access cost. The trade-off — no
  financials — is mitigated by M0 and by the curated demo seeds (§2.6).

### 2.2 SFIO / CBI / NCLT confirmed-fraud labels
- **What / publisher:** Court-confirmed corporate-fraud cases (SFIO investigation
  reports, CBI charge-sheets, NCLT admissions).
- **Access:** Hand-curated JSON,
  [data/labels/sfio_confirmed_frauds.json](../data/labels/sfio_confirmed_frauds.json).
  **14 famous cases** (IL&FS, DHFL, Amtek, etc.).
- **Feeds:** the **only** ground-truth label set — trains the F1a LightGBM meta-learner
  and fits F1b/F1c calibration.
- **Limitations:** n=14 is thin. This single fact is the binding constraint on the
  entire ML tier (see §5.5) — it is enough to *calibrate* but not to train a
  high-variance supervised model.
- **Why court records as labels:** a fraud label must be legally defensible. A model
  trained on "cases that looked suspicious" learns the analyst's bias; a model
  calibrated against *court-confirmed* fraud produces a probability a credit committee
  can defend.

### 2.3 NCLT CP(IB) admitted proceedings
- **What / publisher:** National Company Law Tribunal insolvency (CIRP) admissions and
  winding-up petitions.
- **Access:** Curated seed [infra/seeds/nclt/proceedings.json](../infra/seeds/nclt/proceedings.json)
  with **real case numbers** (e.g. `C.P.(IB) 4258/MB/2019`); auto-refresh scraper
  planned. Parsed by [backend/app/ingest/nclt.py](../backend/app/ingest/nclt.py).
- **Feeds:** **M9** — an admitted CIRP forces `fraud_risk_score ≥ 75`.
- **Limitations:** seed-backed for the demo; the live `nclt-admitted` scraper is planned,
  not running.
- **Why an override floor, not just a feature:** insolvency admission is a binary legal
  fact, not a probabilistic signal. Letting it merely *nudge* a learned score would risk
  a model burying it. A hard floor encodes the domain rule that a court-admitted
  insolvency is, by definition, high risk.

### 2.4 RBI / CIBIL Wilful Defaulter list
- **What / publisher:** RBI-mandated wilful-defaulter declarations, published via CIBIL.
- **Access:** scraper ([backend/app/ingest/rbi_fetcher.py](../backend/app/ingest/rbi_fetcher.py),
  [rbi_html_parser.py](../backend/app/ingest/rbi_html_parser.py)) + curated seed; weekly
  refresh via `.github/workflows/refresh-public-data.yml` planned.
- **Feeds:** **M9** — a wilful-defaulter match forces `fraud_risk_score ≥ 75`.
- **Limitations:** declarations name companies/directors but lag the underlying default.
- **Why:** wilful default is the single strongest public predictor of repeat fraud —
  it is RBI's own formal judgement that a borrower defaulted *while able to pay*.

### 2.5 DGGI press-release archive (ITC carousel topologies)
- **What / publisher:** Directorate General of GST Intelligence enforcement press
  releases (CBIC).
- **Access:** scraper [backend/app/ingest/dggi_press.py](../backend/app/ingest/dggi_press.py);
  **weekly** via `.github/workflows/refresh-public-data.yml`. Five ring topologies
  reconstructed from real busts seeded under
  [infra/seeds/itc_carousel/](../infra/seeds/itc_carousel/).
- **Feeds:** **M4 patterns P8–P12** (ITC carousel detection).
- **Limitations:** DGGI redacts company names during active investigation, so the seed
  files carry real `dggi_zone`, `total_fraud_cr`, `sector`, `case_year` but redacted
  entity names (documented per-file in `entity_disclosure`). The graph *topology* — who
  is the missing trader, where the director overlap sits — is preserved.
- **Why this over a live GSTN feed:** the GSTN ITC feed is restricted to licensed GSPs
  (₹25 lakh capital + MoU with GSTN). The DGGI archive gives real ring structures with
  real amounts and zones, legally publishable, at zero access cost.

### 2.6 CERSAI charges register
- **What / publisher:** Central Registry of Securitisation Asset Reconstruction and
  Security Interest — registered charges (collateral) against borrowers.
- **Access:** scraper [backend/app/ingest/cersai.py](../backend/app/ingest/cersai.py) +
  seed [infra/seeds/cersai/charges.json](../infra/seeds/cersai/charges.json) (manual).
- **Feeds:** **M2** (charges-vs-debt consistency) and **M4 P3/P14** (charge cycling,
  multi-pledge).
- **Limitations:** charge data is registered but not always timely; manual refresh.
- **Why:** CERSAI is the only public window into *collateral* — the difference between
  reported debt and registered charges is a direct tell for multi-pledging and hidden
  borrowing.

### 2.7 BSE SME platform disclosures → industry benchmarks
- **What / publisher:** BSE SME-platform listed-company disclosures, aggregated into NIC
  sector medians/quartiles.
- **Access:** [backend/app/ingest/benchmarks.py](../backend/app/ingest/benchmarks.py) +
  [benchmarks_extended.py](../backend/app/ingest/benchmarks_extended.py); seeds under
  [infra/seeds/benchmarks/](../infra/seeds/benchmarks/).
- **Feeds:** **M5 peer deviation** (z-score of a company's ratios vs its NIC peer group).
- **Limitations:** listed-SME benchmarks approximate the unlisted-SME population.
- **Why:** absolute ratios are meaningless without a peer baseline — a 4% net margin is
  healthy in retail and alarming in software. BSE SME is the closest public proxy for the
  SME peer distribution.

### 2.8 Composite resolver
[backend/app/ingest/composite.py](../backend/app/ingest/composite.py) and
[pipeline.py](../backend/app/ingest/pipeline.py) compose the above into a single
`CompanyBundle`, falling through sources by availability (live API → bulk → seed). This
is why a CIN with no financials still produces a bundle M0 can score — the resolver never
hard-fails on a missing source, it degrades and records the gap in `data_confidence`.

---

## 3. Graph Schema

Neo4j 5 Community + Graph Data Science (GDS) plugin is the **single data store** — no
relational database, no Redis, no separate audit log. Audit records, fraud signals, and
evidence chains all live as graph nodes. Constraints and indexes are applied by
[backend/app/graph/schema.py](../backend/app/graph/schema.py).

### 3.1 Core node labels
| Label | Represents | Key properties |
|---|---|---|
| `Company` | A registered company | `cin` (unique), name, gstin, nic_code, state, `fraud_risk_score`, `p_fraud_calibrated`, `p_fraud_low/high`, `data_confidence`, `last_scored_at` |
| `Director` | A director/DIN | `din` (unique), is_disqualified, num_directorships, GDS centrality (`pagerank_score`, `betweenness_score`, `clustering_coeff`, `ego_density`) |
| `FinancialStatement` | One company-year filing | `(cin, year)` unique, full P&L/BS/CF, auditor fields, going-concern/adverse flags, PDF-forensics metadata |
| `FraudSignal` | **One fired fraud flag** | `signal_id` (unique), signal_type, severity, score_contribution, evidence_string, module_name |
| `DataQualityError` | A validation failure (the audit log) | cin, year, error_type, field, expected/actual |
| `SharedAttribute` | A hyperedge anchor (address/CA/bank) | `(attribute_type, attribute_value)` unique, company_count |
| `IndustryBenchmark` | NIC peer statistics | `(nic_code, year, metric)` unique, median/p25/p75 |
| `NCLTProceeding` | An insolvency/winding-up case | `case_number` unique |
| `WilfulDefaulter` | An RBI/CIBIL declaration | `(cin, bank_name, declared_date)` unique |

### 3.2 Fraud-extension node labels
`GSTEntity`, `ITCClaim`, `MissingTrader` (ITC carousel); `LoanDisbursement`,
`LoanRepayment`, `EvergreeningCluster` (evergreening). These exist so the *same engine*
detects three structurally different frauds without three schemas.

### 3.3 Relationship types (directionality matters)
`IS_DIRECTOR_OF` (Dir→Co), `IS_SHAREHOLDER_OF` (Dir→Co), `HAS_COMMON_DIRECTOR` (Co→Co),
`TRANSACTS_WITH` (Co→Co — **SCC runs on this** for circular trading), `HAS_FINANCIALS`
(Co→FS), `HAS_FRAUD_SIGNAL` (FS→FraudSignal), **`TRIGGERED_BY` (FraudSignal→source node
— the evidence-provenance edge)**, `SHARES_ATTRIBUTE` (Co→SharedAttribute),
`CONNECTED_TO_CRITICAL` (Co→Co — written by belief propagation), `CLAIMS_ITC_FROM`
(GST→GST — **SCC runs on this** for carousels), `FUNDED_REPAYMENT_OF` (the core
evergreening edge), and the ITC/evergreening extension edges.

### 3.4 Why a graph, not a relational store
1. **The fraud *is* the topology.** Circular ITC trading, multi-hop related-party
   chains, and round-trip repayments are cycles and paths. In SQL these are recursive
   self-joins that explode combinatorially; in Cypher + GDS they are first-class
   (`gds.scc`, `gds.wcc`, variable-length path patterns). The detection logic *is* the
   query.
2. **Hyperedges without a junction-table mess.** Shell networks share an address, a CA
   firm, a bank branch. A `SharedAttribute` node models "N companies share this one
   thing" directly; the relational equivalent is a fan of join tables that obscures the
   very pattern you're hunting.
3. **Evidence provenance is native.** A `FraudSignal` node with `TRIGGERED_BY` edges to
   the exact `FinancialStatement`/`TRANSACTS_WITH`/`GSTEntity` that caused it *is* the
   explanation — traversable, not reconstructed. This is why we use it instead of SHAP
   (see §6, §8).
4. **One store, one truth.** PRD §2.1 forbade a second datastore: audit logs are
   `DataQualityError` nodes, caching is node properties (`last_scored_at`). No sync
   problem between a graph and a relational mirror.

### 3.5 How new fraud types extend the schema (the extensibility argument, grounded)
A new fraud type is added by (a) declaring its extension node labels + edges in
[schema.py](../backend/app/graph/schema.py) `CONSTRAINTS`, (b) adding ingestion to
`backend/app/ingest/`, and (c) adding Cypher patterns to
[m04_graph_patterns.py](../backend/app/modules/m04_graph_patterns.py). The scorer,
provenance traversal, calibration, and output layers are **fraud-type-agnostic** — they
operate on `FraudSignal` nodes regardless of which pattern emitted them. This is not a
slide claim: ITC carousel and evergreening were both added this way on top of the core
SME schema, reusing the identical scoring/provenance/output path.

---

## 4. Detection Engine — Rule Modules (M0–M11)

Tier 1 is **deterministic**. These modules do not depend on training data: a
disqualified director is *always* flagged, a circular ITC ring is *always* flagged. They
produce 0–100 scores and `FraudSignal` nodes, and they double as the **feature inputs to
the Tier-2 ML ensemble** (§5). The RiskScorer
([backend/app/scorer.py](../backend/app/scorer.py)) fans out across them with
`asyncio.gather` and aggregates via PRD §7.2 weights.

For each module: behaviour targeted · how it works · origin/citation · what it uniquely
catches · its blind spot · the source that feeds it.

### M0 — Master-data Shell Atlas  *(addition beyond the original M1–M11 design)*
- **Targets:** shell companies detectable from master data alone.
- **How:** one in-memory pass over the data.gov.in CSV computes three signals —
  `ADDRESS_CLUSTER` (N companies at one registered office), `MASS_INCORPORATION` (N
  companies registered the same day in the same state), `PAPER_SHELL` (paid-up < 10% of
  authorised capital AND > 2 years old). Implemented in
  [m0_master_shell_atlas.py](../backend/app/modules/m0_master_shell_atlas.py).
- **Origin:** FATF / FIU shell-company typologies (co-location, mass incorporation,
  dormant paid-up capital).
- **Uniquely catches:** shells among the 191k TN companies that have **zero**
  relationships/financials in the graph — where M1–M11 all skip and the dossier would
  otherwise be empty.
- **Blind spot:** master-data clustering alone is circumstantial; M0 is deliberately
  capped (see scorer `_M0_SEVERITY_THRESHOLDS`) so it never single-handedly pushes a CIN
  to CRITICAL.
- **Feeds from:** §2.1 data.gov.in bulk.
- **Why it exists:** the TN bulk gives breadth (191k cos) but no depth. M0 converts that
  breadth into real signal so `/analyse` on an arbitrary TN CIN returns evidence rather
  than a blank.

### M1 — Beneish M-Score
- **Targets:** earnings manipulation (fabricated P&L).
- **How:** eight ratios (DSRI, GMI, AQI, SGI, DEPI, SGAI, LVGI, TATA); M-Score > −1.78 ⇒
  manipulation. Requires two consecutive years (else skips, weight redistributes).
  [m01_beneish.py](../backend/app/modules/m01_beneish.py).
- **Origin / citation:** **Beneish, M. (1999), "The Detection of Earnings Manipulation,"
  *Financial Analysts Journal*.** A peer-reviewed model with documented out-of-sample
  catches (famously flagged Enron pre-collapse).
- **Uniquely catches:** accrual-based manipulation invisible to a single-year glance.
- **Blind spot:** needs ≥2 years; defeated by consistent multi-year fabrication; tuned on
  US listed firms, so thresholds are approximate for Indian SMEs.
- **Feeds from:** financial statements.
- **Why chosen:** it is the single most validated, citable earnings-manipulation model —
  exactly the academic grounding a forensic report needs.

### M2 — Cross-Statement Consistency (7 checks)
- **Targets:** internal inconsistency across P&L, balance sheet, GST, CERSAI, bank.
- **How:** seven checks, each citing exact numbers — e.g. *Revenue vs GST turnover*
  (|Δ|/rev > 5% ⇒ CRITICAL +40), implied interest rate, cash-conversion ratio,
  depreciation-vs-asset-growth, CWIP staleness, CERSAI-charges-vs-debt (CRITICAL +40),
  bank-credits-vs-revenue. [m02_cross_statement.py](../backend/app/modules/m02_cross_statement.py).
- **Origin:** standard forensic-accounting reconciliation (cross-source triangulation).
- **Uniquely catches:** fabrication that is internally consistent within one statement
  but contradicts an *independent* source (GST, CERSAI, bank).
- **Blind spot:** needs the corroborating source present (GST/bank upload).
- **Feeds from:** financials + GST + CERSAI + bank upload.
- **Why chosen / why it's the highest-weight rule (0.20):** the hardest fraud to commit
  is one that reconciles across mutually-independent government sources. This is the most
  load-bearing deterministic check.

### M3 — Benford's Law
- **Targets:** fabricated numeric distributions.
- **How:** chi-square (p<0.05), Kolmogorov–Smirnov, and Nigrini MAD (>0.012 suspicious,
  >0.020 HIGH); ≥50 numbers; disabled for fixed-price NIC sectors (46, 47, 19, 49, 55).
  [m03_benford.py](../backend/app/modules/m03_benford.py).
- **Origin / citation:** **Nigrini, M. (2012), *Benford's Law: Applications for Forensic
  Accounting, Auditing and Fraud Detection*.**
- **Uniquely catches:** hand-fabricated figures whose leading-digit distribution deviates
  from the natural log distribution.
- **Blind spot:** needs volume (≥50 numbers); inapplicable to fixed-price/assigned-number
  domains — hence the NIC exclusion list.
- **Feeds from:** all numeric fields across financials.
- **Why low weight (0.05):** a strong corroborator but high false-positive rate alone;
  it earns its weight only in combination, which is exactly what the meta-learner learns.

### M4 — Graph Pattern Detection (17 Cypher patterns)
- **Targets:** all three fraud types' *structural* signatures.
- **How:** 17 Cypher/GDS queries — **P1–P7** core SME (Circular Trading SCC, Related-Party
  Director Overlap, Disqualified Director Active, Spider Web, Multi-Hop Chain, Revenue
  Concentration, Entity Resolution WCC); **P8–P12** ITC carousel (Ring SCC, Missing
  Trader, Cancelled GSTIN, New-GSTIN-High-ITC, Multi-Hop ITC+Director); **P13–P17**
  evergreening (Round-Trip Repayment, Serial Charge Cycling, Shell Conduit, NPA Vintage
  Mismatch, Multi-Entity Exposure). Each match writes a `FraudSignal` with `TRIGGERED_BY`
  edges. [m04_graph_patterns.py](../backend/app/modules/m04_graph_patterns.py).
- **Origin:** GDS strongly-connected-components (Tarjan) and weakly-connected-components;
  classic AML circular-trading and layering typologies.
- **Uniquely catches:** the topological fraud no tabular model can see — cycles, chains,
  shared-control rings.
- **Blind spot:** requires a populated live graph (skips cleanly when the Neo4j driver is
  absent); pattern thresholds are tuned to the seed scale.
- **Feeds from:** the whole graph.
- **Why central:** this is the Neo4j-track core — the patterns *are* the differentiated
  capability. (Note: M4 is excluded from the *meta-learner* feature vector — see §5.2 —
  because it is async + driver-dependent; its signal flows through `fraud_risk_score`,
  not the L2 stack.)

### M5 — Peer Deviation
- **Targets:** ratios abnormal for the company's industry.
- **How:** z-score per metric vs BSE SME NIC benchmarks (p25/median/p75); score grows
  with count and magnitude of outliers. [m05_peer_deviation.py](../backend/app/modules/m05_peer_deviation.py).
- **Origin:** standard peer-relative anomaly analysis.
- **Uniquely catches:** a company whose numbers are internally consistent but wildly
  off-sector.
- **Blind spot:** needs a benchmark for the NIC code; sector misclassification misleads it.
- **Feeds from:** §2.7 BSE benchmarks.

### M6 — Temporal Behavioural Signals (8)
- **Targets:** suspicious *timing* — director exit pre-loan, auditor change, sole-prop
  auditor (CRITICAL), filing-delay-with-sudden-improvement, quarterly smoothing (CoV<0.03),
  round-trip borrowing, increasing filing delay, post-bad-year policy change.
  [m06_temporal.py](../backend/app/modules/m06_temporal.py).
- **Uniquely catches:** behavioural choreography around an event (a loan application) that
  any single-snapshot view misses.
- **Blind spot:** needs a multi-year history.

### M7 — Auditor NLP
- **Targets:** audit-opinion red flags + auditor collusion networks.
- **How:** spaCy keyword extraction on the audit-opinion paragraph (going-concern, adverse
  opinion → CRITICAL, emphasis-of-matter, vague related-party notes) + Neo4j auditor-density
  analysis (one DIN signing >50 SME filings in a district flags all their clients).
  [m07_auditor_nlp.py](../backend/app/modules/m07_auditor_nlp.py).
- **Origin:** audit-report linguistic analysis + auditor-network risk.
- **Uniquely catches:** the auditor *telling you* there's a problem (going-concern), and
  rubber-stamp auditor rings.
- **Blind spot:** needs the opinion text; keyword approach misses paraphrase.

### M8 — Document Forensics
- **Targets:** forged/spliced filing PDFs.
- **How:** PDF producer (MS Word/Google Docs on a statutory filing), creation-vs-mod date
  gap, cross-page font inconsistency, image-DPI inconsistency.
  [m08_document_forensics.py](../backend/app/modules/m08_document_forensics.py).
- **Uniquely catches:** documents assembled/edited rather than system-generated.
- **Blind spot:** a cleanly re-printed forgery defeats metadata checks.

### M9 — NCLT / DRT / Wilful Defaulter
- **Targets:** companies already in formal legal jeopardy.
- **How:** admitted CIRP → **force score ≥ 75**; winding-up petition → HIGH; wilful
  defaulter → **force score ≥ 75**; DRT case → HIGH. Override applied in the scorer with
  the matched `signal_id`s recorded for the audit trail.
  [m09_nclt_defaulter.py](../backend/app/modules/m09_nclt_defaulter.py).
- **Uniquely catches:** the legal ground-truth a learned score must never bury.
- **Why an override, not a feature:** see §2.3.
- **Feeds from:** §2.3 NCLT, §2.4 RBI/CIBIL.

### M10 — Hypergraph Shell Network Detection
- **Targets:** shell networks invisible to pairwise edges.
- **How:** `SharedAttribute` nodes (address >5 cos, CA-DIN >50 cos/district, IFSC >10
  related cos) reveal multi-company collusion. Runs as a **cross-company batch**, not
  per-request — the scorer reads precomputed results from the analytics cache.
  [m10_hypergraph_shell.py](../backend/app/modules/m10_hypergraph_shell.py).
- **Uniquely catches:** rings that share infrastructure without ever transacting directly.
- **Blind spot:** needs the cross-company batch precompute; per-request scoring of a lone
  bundle finds nothing (by design).

### M11 — Anomaly Detection (Isolation Forest + LOF)
- **Targets:** novel patterns the trained ensemble has never seen.
- **How:** Isolation Forest on a 20-dim financial-ratio vector (decision-function < −0.10
  ⇒ `NOVEL_PATTERN_FINANCIAL`) and Local Outlier Factor on the 7-dim graph-feature vector
  (`negative_outlier_factor_` < −1.50 ⇒ `NOVEL_PATTERN_GRAPH`), both fit against a
  background of healthy peers. [m11_anomaly.py](../backend/app/modules/m11_anomaly.py).
- **Origin / citation:** **Liu, Ting & Zhou (2008), "Isolation Forest";** **Breunig et
  al. (2000), "LOF: Identifying Density-Based Local Outliers."**
- **Uniquely catches:** the unknown-unknown — fraud whose shape no rule and no labelled
  detector encodes.
- **Why two detectors on two feature spaces:** Isolation Forest sees *suspicious numbers*;
  LOF sees *suspicious graph structure*. A fraud with clean financials but an anomalous
  director network passes IsoForest and fails LOF. Both are required. **This is the same
  insight that makes the deleted D2 β-VAE redundant — see §5.5.**

### Belief Propagation (cross-cutting, post-scoring)
- **Targets:** risk that should propagate from a confirmed-bad company to its cluster.
- **How:** loopy belief propagation on the bipartite Company↔`SharedAttribute` graph
  ([ml/belief_propagation.py](../ml/belief_propagation.py)). A CRITICAL seed lifts a
  fractional share of its risk to other cluster members; writes `CONNECTED_TO_CRITICAL`
  edges and returns a `propagation_band`/`propagation_score` on the report.
- **Origin / citation:** **Pearl (1988), belief propagation** — here the loopy bipartite
  variant, avoiding a full junction-tree.
- **Uniquely catches:** the not-yet-flagged shell sharing an address/auditor/branch with a
  known fraud — lifts unlabelled cluster CINs to MEDIUM (PRD §10 Day-13 acceptance).
- **Why included as first-class:** it is wired in the live scoring path and is the
  mechanism that turns "this one company is bad" into "this *network* is bad" — the entire
  point of a graph approach.

---

## 5. Detection Engine — ML Ensemble (4 live detectors + meta-learner)

> **Reality statement.** The PRD specified six L1 detectors (D1–D6). **Four are live:**
> D3, D4, D5, D6. **D1 (CatBoost) and D2 (β-VAE) were removed on 2026-05-21** as
> unimplemented stubs. §5.5 proves this removed no coverage; §10 gives the effort to
> restore them.

Tier 2 learns the *optimal combination* of the Tier-1 signals plus the detector scores.
The design rationale (PRD §5, lines 76 & 473): rules catch the known, the meta-learner
learns the combination, the anomaly detectors catch the novel.

### 5.1 The four live detectors
Same structure each: anomaly class · architecture · origin · why-over-alternative · what
it catches that the rules don't.

**D3 — Temporal Graph Network (TGN).**
- *Class:* temporal graph-structure anomaly.
- *Architecture:* PyTorch-Geometric `TGNMemory` + graph-attention embedding, 2-layer,
  memory_dim=64, embedding_dim=64, edge-pruning at weight<0.05; inductive mean-aggregation
  fallback for unseen entities. [ml/detectors/d3_tgn.py](../ml/detectors/d3_tgn.py).
- *Origin / citation:* **Rossi et al. (2020), "Temporal Graph Networks for Deep Learning
  on Dynamic Graphs."**
- *Why TGN over a static GCN (the thing it replaced):* fraud rings *evolve* — directors
  join, transactions cycle, shells spin up. A static GCN sees one snapshot; TGN models the
  temporal evolution of the director-company graph, so it catches a ring forming, not just
  a ring that already exists.
- *Catches beyond rules:* learned structural signatures that no hand-written Cypher pattern
  enumerates.

**D4 — Local Outlier Factor on graph features.**
- *Class:* unsupervised graph-topology outlier.
- *Architecture:* sklearn `LocalOutlierFactor`, n_neighbors=20, on the L0.5 7-feature
  matrix (PageRank, betweenness, clustering coeff, director count, counterparty age,
  degree, ego-density), score normalised to [0,1]. [ml/detectors/d4_lof.py](../ml/detectors/d4_lof.py).
- *Origin / citation:* **Breunig et al. (2000), LOF.**
- *Why:* density-based local outlier detection finds companies in *unusual graph
  neighbourhoods* even with clean books — the structural complement to financial anomaly.
- *Note:* D4 is the same LOF that M11 runs as a rule — here it is wired as a detector
  feeding the meta-learner. The duplication is intentional: M11 emits a deterministic
  `FraudSignal`; D4 contributes a continuous score the meta-learner can weight.

**D5 — Mamba SSM (sequence), with TCN fallback.**
- *Class:* transaction-sequence anomaly.
- *Architecture:* per-company sequences of (amount, counterparty_type, gst_status,
  timestamp_delta), padded/truncated to 128; 2-layer Mamba (state_dim=64) on CUDA, with a
  **Temporal Convolutional Network fallback** for CPU inference (Mamba is Linux+CUDA only).
  [ml/detectors/d5_mamba.py](../ml/detectors/d5_mamba.py).
- *Origin / citation:* **Gu & Dao (2023), "Mamba: Linear-Time Sequence Modeling with
  Selective State Spaces."**
- *Why Mamba over an RNN/Transformer:* linear-time selective state space handles long
  transaction sequences cheaply; the TCN fallback guarantees the *production* box (CPU)
  still scores when Mamba's CUDA kernels are unavailable.
- *Catches beyond rules:* sequential laundering rhythms (round-tripping cadence) invisible
  to a static snapshot.

**D6 — Combined Autoencoder (tabular + graph).**
- *Class:* joint reconstruction anomaly.
- *Architecture:* concat [20 financial + 7 graph] = 27-dim, each group normalised to
  [0,1]; encoder [27→64→32→16] + mirrored decoder, ReLU/BatchNorm/dropout=0.2;
  reconstruction error → [0,1] via 99th-percentile normalisation.
  [ml/detectors/d6_combined_ae.py](../ml/detectors/d6_combined_ae.py).
- *Origin:* reconstruction-error anomaly detection.
- *Why combined:* by reconstructing financial **and** graph features jointly, D6 catches
  anomalies in the *interaction* between a company's books and its network — a profile that
  is normal on each axis but abnormal jointly. **This is precisely why the deleted D2
  (tabular-only β-VAE) added no coverage: D6 already reconstructs the financial features.**

### 5.2 The meta-learner stack (F1a → F1b → F1c)
- **F1a — LightGBM OOF meta-learner.** K=5 stratified out-of-fold: each training sample's
  prediction comes from a model that never saw its label (leakage-free); a final model on
  all data serves inference. [ml/meta/f1a_lightgbm_oof.py](../ml/meta/f1a_lightgbm_oof.py).
  **Replaces the deprecated static formula** `S = 0.4·GNN + 0.2·VAE + …` entirely — the
  weights are *learned*, not hand-set.
- **Feature vector** ([ml/features.py](../ml/features.py)): 10 modules × 3 features
  (score, max-severity ordinal, signal-count) + 11 bundle-level features + 4 detector
  scores (`d3..d6`). **M4 is deliberately excluded** from the L2 vector — it is async and
  needs a live Neo4j driver, which the offline OOF retrain lacks; including it as a
  constant-0 at train time would create train/infer asymmetry and mislead F1a. M4's signal
  still reaches the user through `fraud_risk_score`.
- **F1b — Isotonic calibration.** See §6.
- **F1c — Split-conformal intervals.** See §6.
- **Inference bridge:** [backend/app/ml_inference.py](../backend/app/ml_inference.py) loads
  the three artefacts lazily, guards a train/infer **feature-width mismatch** loudly (a
  silent null here is the worst-case bug — green dashboard, every `p_fraud_calibrated`
  null), and returns nulls cleanly when artefacts are absent (PRD §7.1 allows that).

### 5.3 Why a heterogeneous ensemble, not one strong model
A single model has a single blind spot. The four detectors occupy **deliberately
different representations** — temporal graph (D3), static graph topology (D4), transaction
sequence (D5), joint tabular+graph reconstruction (D6). A fraud that fools one
representation is unlikely to fool all four. With the deterministic rules feeding the same
meta-learner, a sophisticated fraud must simultaneously look normal to the rules, to the
graph models, to the sequence model, and to the reconstruction model.

### 5.4 Why OOF stacking + LightGBM as meta-learner
- **Why OOF (out-of-fold) stacking:** stacking detector outputs naively leaks — a detector
  that saw a sample predicts it optimistically, and the meta-learner learns that optimism.
  K=5 OOF guarantees every L2 training row was predicted by a model blind to its label.
  With only 14 labels, leakage would be catastrophic; OOF is non-negotiable here.
- **Why LightGBM specifically:** the L2 input is low-dimensional, mixed-scale tabular
  (rule scores + detector scores). Gradient-boosted trees dominate on exactly this shape,
  handle non-linear interactions between detectors, are robust to unscaled features, and
  train in milliseconds — letting the OOF retrain run on every label update. (LightGBM's
  native SHAP is retained **internally only**, for meta-learner debugging; it is never
  surfaced — provenance is the user-facing explanation.)

### 5.5 Is a 4-detector ensemble sufficient? (the decision, with evidence)
**Yes — for the current data regime. Dropping D1/D2 removed no coverage.** The six
PRD detectors were each meant to own a feature space:

| Feature space | PRD detector | Covered now by |
|---|---|---|
| Supervised tabular | D1 CatBoost | **F1a meta-learner itself** (a supervised LightGBM on the same features+labels) |
| Unsupervised tabular | D2 β-VAE | **M11 Isolation Forest** (20 financial ratios) **+ D6** (reconstructs the 20 financial features) |
| Temporal graph | D3 TGN | D3 (live) |
| Graph topology | D4 LOF | D4 (live) |
| Sequence | D5 Mamba/TCN | D5 (live) |
| Combined tabular+graph | D6 AE | D6 (live) |

- **D2 (β-VAE) is redundant.** Its only job is reconstruction error on the 20 financial
  features — already covered twice (M11 IsoForest detects anomalous financial ratios; D6's
  autoencoder reconstructs those same features as part of its 27-dim input). D2's sole
  unique offering is disentangled latent factors for interpretability, and this system
  derives interpretability from graph provenance, not latent vectors. **Coverage loss ≈ 0.**
- **D1 (CatBoost) is statistically unsound at n=14, not merely unbuilt.** It is a
  supervised booster — but F1a is *already* a supervised gradient-boosted model on the same
  14 labels. A second booster on the same features+labels is a near-duplicate learner and a
  real OOF-leakage risk at this label count. It duplicates F1a rather than adding a feature
  space. **It becomes worth building only once the label set reaches ~100+ confirmed cases**
  — below that, it adds variance and leakage, not signal.

**Conclusion:** the four live detectors + M11's Isolation Forest functionally span all six
feature spaces. The binding constraint on the ML tier is **label volume (14)**, not detector
count. See §10 for the effort to restore D1/D2/MAPIE if/when that constraint lifts.

---

## 6. Calibration & Uncertainty Layer

A raw model score is not a probability. A credit committee or a court needs a number where
**0.7 actually means a 70% fraud rate** — otherwise the score is indefensible. This layer
turns F1a's raw output into a calibrated, interval-bounded probability.

### 6.1 Raw score → Isotonic regression (F1b)
- **What:** `sklearn.isotonic.IsotonicRegression` fit on a **separate 15% hold-out** (not
  the OOF data — fitting on OOF would leak the K-fold target distribution into the
  calibrator). Validated by reliability diagram: P(fraud)=0.8 should correspond to ~80%
  observed fraud rate. [ml/meta/f1b_isotonic.py](../ml/meta/f1b_isotonic.py).
- **Why isotonic over Platt scaling:** Platt (sigmoid) calibration assumes the miscalibration
  has a specific sigmoidal shape. Isotonic regression assumes only **monotonicity** —
  higher raw score ⇒ higher-or-equal calibrated probability — and otherwise fits the
  empirical reliability curve non-parametrically. For a tree-ensemble (LightGBM), whose raw
  scores are *not* sigmoid-distorted, isotonic fits the actual curve rather than forcing a
  sigmoid. The cost (isotonic needs more calibration data and can overfit on tiny sets) is
  mitigated by the dedicated hold-out and the small, monotone target.

### 6.2 Conformal prediction interval (F1c) — split conformal at α=0.10
- **What:** wraps the calibrated classifier and emits P(fraud) plus `[P_low, P_high]` at
  **α=0.10**, i.e. a **90% interval** with empirical coverage on the calibration set, using
  split-conformal residuals on the (calibrated-probability, label) pair with the standard
  (n+1)/n finite-sample correction. [ml/meta/f1c_split_conformal.py](../ml/meta/f1c_split_conformal.py).
- **What α=0.10 means operationally:** the interval is constructed so that, over repeated
  use, the true label falls inside `[P_low, P_high]` at least ~90% of the time. A *wide*
  interval is itself a signal — "the model is uncertain about this one; route to manual
  review."
- **Why split conformal, not MAPIE (which the PRD named):** the `mapie>=1.0` classification
  interface was unstable at implementation time. The construction here is mathematically
  identical to MAPIE's split-conformal approach on a probability target; `f1c_mapie.py`
  remains only as a joblib-unpickling shim, and swapping the library back in is a one-line
  change once its API stabilises (see §10). **No capability is lost** — the 90% coverage
  guarantee is delivered today.
- **Why conformal at all (the legal argument):** conformal prediction gives a
  *distribution-free, finite-sample* coverage guarantee — it does not assume the data is
  Gaussian or that the model is correct. That is exactly the property a forensic report
  needs: "we are 90%-confident the fraud probability lies in this range" is defensible in a
  way a bare point estimate is not.

### 6.3 Training/calibration set: the 14 SFIO labels
- **Why 14 is enough to *calibrate* even though it is thin to *train*:** calibration fits a
  low-parameter monotone map (isotonic) and a single conformal quantile — both are
  low-variance estimators that tolerate small n far better than a high-capacity supervised
  model would. The same 14 labels are insufficient to train a second supervised booster
  (the D1 argument in §5.5) but sufficient to calibrate the one model we do train. This is
  the honest boundary of the ML tier, and it is stated plainly rather than hidden.
- The whole layer degrades gracefully: when artefacts are absent, `p_fraud_calibrated` and
  `p_fraud_interval` return `null` and the deterministic `fraud_risk_score` still stands
  (PRD §7.1).

---

## 7. Coverage Matrix

**The most important section: is the detector set sufficient?** Rows are fraud behaviours;
columns are the components that fire for each. ✅ = primary detector; ◾ = secondary/
corroborating. A row with only one mark is a thin spot; rows with 2–3+ marks demonstrate
redundancy. (M0–M11 = rule modules; BP = belief propagation; D3–D6 = ML detectors; ML =
F1a meta-learner aggregating all.)

| Fraud behaviour | M0 | M1 | M2 | M3 | M4 | M5 | M6 | M7 | M8 | M9 | M10 | M11 | BP | D3 | D4 | D5 | D6 | ML |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Earnings manipulation (fabricated P&L) | | ✅ | ✅ | ✅ | | ◾ | | | | | | ◾ | | | | | ◾ | ✅ |
| Revenue inflation vs GST/bank | | | ✅ | | | | | | | | | | | | | ◾ | | ✅ |
| Balance-sheet / asset inflation (CWIP) | | ✅ | ✅ | | | ◾ | | | | | | ◾ | | | | | ◾ | ✅ |
| Circular ITC trading (carousel) | | | | | ✅ | | | | | | ◾ | | ◾ | ✅ | ◾ | ◾ | | ✅ |
| Missing trader | | | | | ✅ | | | | | | | | | | | | | ✅ |
| Cancelled-GSTIN ITC claim | | | | | ✅ | | | | | | | | | | | | | ✅ |
| Shell-company structures | ✅ | | | | ✅ | | | | | | ✅ | ◾ | ◾ | ◾ | ✅ | | ◾ | ✅ |
| Director overlap / interlocking boards | | | | | ✅ | | ◾ | | | | ◾ | | ◾ | ✅ | ◾ | | | ✅ |
| Disqualified director active | | | | | ✅ | | | | | ◾ | | | | | | | | ✅ |
| Collateral multi-pledge / charge cycling | | | ✅ | | ✅ | | ◾ | | | | | | | | | | | ✅ |
| Loan evergreening / round-trip repayment | | | ◾ | | ✅ | | ◾ | | | | | | ◾ | ◾ | | ✅ | | ✅ |
| Wilful default / NCLT propagation | | | | | ◾ | | | | | ✅ | ◾ | | ✅ | | | | | ✅ |
| Auditor collusion / sole-proprietor ring | | | | | ◾ | | ◾ | ✅ | | | ✅ | | ◾ | | ◾ | | | ✅ |
| Document forgery (spliced PDF) | | | | | | | | | ✅ | | | | | | | | | ✅ |
| Address clustering / mass incorporation | ✅ | | | | ◾ | | | | | | ✅ | ◾ | ◾ | | ◾ | | | ✅ |
| Temporal manipulation (smoothing, delays) | | ◾ | | | | | ✅ | | | | | | | ◾ | | ✅ | | ✅ |
| **Novel / unknown anomaly** | | | | | | | | | | | | ✅ | | ◾ | ✅ | ◾ | ✅ | ✅ |

**Reading the matrix.** Every behaviour row has at least two independent detectors, and the
"novel/unknown" row — the open-set risk — is covered by M11 + D3/D4/D5/D6, the unsupervised
quartet. **No row depends on the removed D1/D2:** the supervised-tabular space is held by
the ML meta-learner column, and the unsupervised-tabular space by M11 + D6. The thinnest
rows (single ✅) are *legal-fact* rows (missing trader, cancelled GSTIN, disqualified
director, document forgery) — these are deterministic binary facts where redundancy is
neither needed nor meaningful; one authoritative detector is correct by construction.

---

## 8. Output Layer

Every output is generated from the graph and traces back to it. No number originates in a
model's prose; numbers come from `FraudSignal` nodes and their `TRIGGERED_BY` sources.

### 8.1 Output types
| Output | Generated by | Primary consumer | Decision it supports | Technical literacy assumed |
|---|---|---|---|---|
| **Dual-output JSON** (`fraud_risk_score`, `risk_band`, `p_fraud_calibrated`, `p_fraud_interval`, `data_confidence`, `ensemble_disagreement_flag`, `evidence_chain`, `module_breakdown`, override fields, propagation band) | [scorer.py](../backend/app/scorer.py) → `/analyse/{cin}` | All (API/dashboard) | Approve / review / reject; triage queue ordering | Low (dashboard renders it) |
| **Analysis dashboard** | [Dashboard.tsx](../frontend/src/pages/Dashboard.tsx) | Loan Officer | Lend / decline / escalate | Low |
| **Graph Explorer** (d3-force) | [GraphExplorer.tsx](../frontend/src/pages/GraphExplorer.tsx) | Investigator | Trace the ring; identify the controller | Medium |
| **Evidence provenance chain** | `/analyse/{cin}/provenance` ([api/analyse.py](../backend/app/api/analyse.py)), persisted by [graph/writes.py](../backend/app/graph/writes.py) | Forensic Auditor | Build the legal narrative | High |
| **ITC Carousel / Evergreening views** | [ITCCarousel.tsx](../frontend/src/pages/ITCCarousel.tsx), [Evergreening.tsx](../frontend/src/pages/Evergreening.tsx) | Investigator | Confirm ring topology | Medium |
| **Shell Atlas** | [ShellAtlas.tsx](../frontend/src/pages/ShellAtlas.tsx) ← M0 / `/shells` | Investigator / Admin | Browse master-data shell clusters | Medium |
| **Forensic PDF** | [api/report.py](../backend/app/api/report.py) (reportlab) | Forensic Auditor / committee | File / archive a defensible record | Low–Medium |
| **Narrative prose** | [narrative.py](../backend/app/narrative.py) (Mistral, template fallback) | Loan Officer | Quick human-readable summary | Low |
| **`/sources` lineage** | [api/sources.py](../backend/app/api/sources.py) | Judge / Auditor | Audit data provenance | Low |

### 8.2 What each persona *sees* (literacy-matched)
- **Loan Officer** sees a band (CRITICAL/HIGH/…), the calibrated probability with its
  interval, the DataConfidence %, and a plain-language narrative — enough to decide without
  reading Cypher.
- **DGGI Investigator** sees the Graph Explorer and ITC/Evergreening views — the ring
  topology, the missing trader, the director overlap — the structure, not just the score.
- **Forensic Auditor** sees the full provenance chain and the PDF: every `FraudSignal` with
  its exact triggering data point, suitable for a filing.

### 8.3 Evidence provenance — why it traces to nodes and edges (legal defensibility)
Every finding is a `FraudSignal` node whose `evidence_string` cites **specific numbers**
(never "revenue appears inflated"; always *"Revenue per P&L (₹12.4 cr) exceeds GST taxable
turnover (₹8.2 cr) by 51.2% — above 5% tolerance"*) and whose `TRIGGERED_BY` edges point to
the exact `FinancialStatement`/`TRANSACTS_WITH`/`GSTEntity`/`Charge` that caused it. The
scorer persists these to Neo4j so `/provenance` traverses the live graph rather than
re-scoring. **Why this instead of SHAP:** a SHAP value explains a model's *internal*
weighting; it cannot be handed to a court as evidence of fraud. A typed graph path from a
flag to a government data row *is* the evidence. The reportlab PDF carries a UUID + UTC
timestamp + disclaimer and is JWT-gated — an auditable artefact, not a screenshot.

---

## 9. Persona × Input × Output Matrix

Roles are enforced server-side ([backend/app/auth/models.py](../backend/app/auth/models.py),
`UserRole = credit_officer | investigator | auditor | admin`; admin is not
self-registrable). One glance per role:

| Persona (role) | Inputs | Sees | Decision made | Exports |
|---|---|---|---|---|
| **NBFC Loan Officer** (`credit_officer`) | CIN search; optional GST + bank-statement upload | Dashboard: band, calibrated P(fraud)+interval, DataConfidence %, narrative, evidence summary | Lend / decline / escalate to investigation | PDF report |
| **DGGI Investigator** (`investigator`) | CIN / GSTIN; ITC ring exploration | Graph Explorer, ITC Carousel & Evergreening views, full evidence chain | Confirm a ring; identify controller; open a case | PDF + graph export |
| **Forensic Auditor** (`auditor`) | CIN; post-incident records | Full provenance chain, module breakdown, override audit trail (which `signal_id` forced the floor) | Build legal narrative; quantify exposure | Forensic PDF (UUID + timestamp + disclaimer) |
| **Admin** (`admin`, seeded only) | All of the above + data/source management | Shell Atlas, `/sources` health, all views | Operate the platform; manage refresh | All |

The journey is uniform: **input a CIN → bundle resolved across sources (§2.8) → scored by
the engine (§4–§5) → calibrated (§6) → rendered to the role's literacy (§8).** The same
dual-output payload backs every persona view; only the *presentation* differs.

---

## 10. Known Limitations & Honest Gaps

Stated plainly — a team that knows its blind spots is more trustworthy than one that
oversells. Items here are deferred or not-live, with the reason and (where relevant) the
effort to close.

### 10.1 ML detectors deferred (the D1/D2 decision)
| Item | Status | Why deferred | Effort to build | Worth building when |
|---|---|---|---|---|
| **D2 β-VAE** (unsupervised tabular) | Removed (was a stub) | Subsumed by M11 Isolation Forest + D6 autoencoder, which already cover the financial-feature reconstruction space (§5.5) | ~0.5–1 day (reuse D6 scaffold) | Only if disentangled latent factors are ever wanted for their own sake — low priority |
| **D1 CatBoost** (supervised tabular) | Removed (was a stub) | Duplicates the F1a LightGBM meta-learner and risks OOF leakage at n=14 labels; adds variance, not coverage (§5.5) | ~0.5 day to wire | **Above ~100+ confirmed labels** — below that it is statistically unsound, not just unbuilt |
| **MAPIE conformal library** | Replaced by hand-rolled split conformal | `mapie>=1.0` classification API was unstable; the in-house construction delivers the identical α=0.10 / 90%-coverage guarantee today | ~0.5 day (one-line swap once API stabilises) | If class-conditional / Mondrian coverage is ever needed |

**Net:** the 4-detector engine has no coverage gap (§7). The binding constraint is **label
volume (14 SFIO cases)**, which limits supervised capacity — not detector count.

### 10.2 Data sources not live in the deployment
| Item | Why | Workaround |
|---|---|---|
| MCA21 V3 live API | Paid (~₹5–20k/mo), out of budget | data.gov.in CC-BY TN bulk (191k cos) + composite fall-through |
| GSTN live ITC feed | Restricted to licensed GSPs (₹25 lakh + MoU) | DGGI press-release archive — real bust topologies |
| MCA Public Portal live scrape | Playwright/Chromium too heavy for the 4 GB Lightsail box | local-dev only ([INGEST_MCA_PUBLIC.md](./INGEST_MCA_PUBLIC.md)) |
| Live NCLT / RBI scrapers | Built but not scheduled in prod | curated real-case seeds; weekly CI refresh planned |
| Mistral narrative | Optional free-tier key | deterministic template fallback (cites only structured numbers; never hallucinates) |

### 10.3 Modelling / scope limitations
- **Tier-1 weights M10/M11 are reserved** in the scorer but fire only via the cross-company
  analytics cache; per-request scoring of a lone bundle skips them by design.
- **M4 is excluded from the meta-learner feature vector** (async + Neo4j-dependent); its
  signal flows through `fraud_risk_score`, not the L2 stack (§5.2).
- **Beneish thresholds** are tuned on US listed firms; approximate for Indian SMEs.
- **Benford** is disabled for fixed-price NIC sectors and needs ≥50 numbers.
- **Coverage scope:** companies only — LLPs/partnerships, cross-border/offshore structures,
  and consortium signal-sharing are out of scope (PRD §15 future work).
- **`ensemble_disagreement_flag=True` on all demo CINs** at seed scale (LOCAL_TEST_REPORT
  F5) — plausibly expected at this fixture size; flagged, not yet root-caused.

### 10.4 Known open issues (from [LOCAL_TEST_REPORT.md](./LOCAL_TEST_REPORT.md))
Tracked findings include auth-gating on `/analyse` (F4), duplicate-email registration
(F3), and the login email prefill (F1). See that report for status and fix order.

---

## Appendix — Source map (file → responsibility)
- Orchestration: [backend/app/scorer.py](../backend/app/scorer.py)
- ML inference bridge: [backend/app/ml_inference.py](../backend/app/ml_inference.py)
- Feature builder: [ml/features.py](../ml/features.py)
- Meta stack: [ml/meta/](../ml/meta/) (f1a_lightgbm_oof, f1b_isotonic, f1c_split_conformal)
- Detectors: [ml/detectors/](../ml/detectors/) (d3_tgn, d4_lof, d5_mamba, d6_combined_ae)
- Rule modules: [backend/app/modules/](../backend/app/modules/) (m0 … m11)
- Belief propagation: [ml/belief_propagation.py](../ml/belief_propagation.py)
- Graph schema: [backend/app/graph/schema.py](../backend/app/graph/schema.py)
- Ingestion: [backend/app/ingest/](../backend/app/ingest/)
- Provenance / persistence: [backend/app/graph/writes.py](../backend/app/graph/writes.py)
- Frozen requirements: [Sentinel_G_Final.docx](../Sentinel_G_Final.docx) (PRD v4.0)
