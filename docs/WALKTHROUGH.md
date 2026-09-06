# FinTrace — Investigation Walkthrough

> A role-neutral, end-to-end tour of the VeriLens application for local QA and
> hackathon demonstrations.

Start the local Neo4j/GDS, FastAPI, and Vite services as described in
`docs/RUNNING.md`. For the hosted demonstration, open the deployed dashboard
directly. The walkthrough does not depend on a login or account setup.

## Section 0 — Test data

Use the curated fixture CINs below to reproduce the main investigation paths:

| CIN | Surface | Expected result |
|---|---|---|
| `U45201MH2005PTC155294` | SME fraud dashboard and provenance | CRITICAL fixture with graph evidence |
| `L65910MH1984PLC032662` | Evergreening | DHFL loan-flow fixture with repayment patterns |
| `U27109MH2018PTC312456` | ITC carousel | DGGI topology fixture |
| `L85110KA1981PLC013115` | Clean control | LOW fixture |

The optional data.gov.in bulk index adds master-data companies to local search.
It is not required for the curated demo. See
`docs/INGEST_DATA_GOV_IN.md` for the operator runbook.

## Section 1 — Dashboard and fraud analysis

1. Open the application dashboard and use the Search view.
2. Enter `U45201MH2005PTC155294` and open the analysis.
3. Verify the CRITICAL band, fraud-risk score, data confidence, calibrated
   probability when model artifacts are available, and conformal interval.
4. Check that the company metadata identifies Construction (NIC 45201) and
   that evidence cards contain specific values rather than generic prose.
5. Use the severity chips to filter the evidence chain, then restore all
   signals.

The result is an investigation aid. It is not a guarantee of fraud and should
be reviewed alongside the underlying records.

## Section 2 — Graph and provenance

1. Open the Graph Explorer for `U45201MH2005PTC155294`.
2. Expand the director and related-company connections.
3. Select a `FraudSignal` node and inspect its evidence string.
4. Open the provenance view or export JSON from
   `/analyse/{cin}/provenance/export`.

Verify that signals connect through `TRIGGERED_BY` relationships to the source
records represented in the graph. This is the application's traceable
investigation artifact; it does not replace independent review of source data.

## Section 3 — ITC carousel

1. Open `/itc`.
2. Verify the ring topology and the DGGI source disclosure.
3. Inspect the GST entities, invoice edges, turnover, tax-paid values, and
   per-node analysis cards.
4. Confirm that redacted company names and fixture-backed topology are clearly
   identified.

The DGGI topology is seeded from published press-release information. The
application does not claim unrestricted live GSTN retrieval.

## Section 4 — Evergreening

1. Open `/evergreening`.
2. Verify the seeded DHFL loan-flow graph and the
   `FUNDED_REPAYMENT_OF` relationships.
3. Inspect the round-trip and related-party pattern evidence.
4. Open the linked analysis or provenance view and confirm the values shown in
   the metrics and evidence cards.

The displayed pattern is fixture-backed when the production graph is not
available. It should be read as a reproducible investigation scenario, not as
continuous external monitoring.

## Section 5 — Upload and report generation

1. Open `/upload` and submit a supported financial PDF or structured GST/bank
   overlay for a known CIN.
2. Confirm the upload preview and rerun the analysis to see the overlay folded
   into the bundle and data-confidence calculation.
3. Open `/reports`, choose a known CIN, and download the generated PDF.
4. Verify the report ID, UTC timestamp, score, confidence, interval, disclaimer,
   and evidence excerpt.

The PDF is a traceable investigation artifact generated from the analysis
payload. It should be reviewed against the underlying records and is not a
guaranteed finding.

## Section 6 — Health and service verification

Check these endpoints in a local browser or with `curl`:

1. `/health` — application readiness.
2. `/health/ml` — availability of the local model artifacts.
3. `/health/neo4j` — reachability of the local Neo4j/GDS service.
4. `/gonka/models` — configured GonkaRouter model availability when the backend
   key is present.

For AI claim verification, open the Gonka Verification view, submit claim text,
and confirm that the response includes a verdict, confidence, rationale,
caveats, and Gonka request ID. Gonka receives the supplied claim text; the
verification endpoint does not independently retrieve external evidence.

## Section 7 — Current application access model

The current deployed application does not enforce the previous JWT-based
role-access workflow described in older versions of this walkthrough. Do not
use legacy account, registration, role-assignment, or permission-matrix
instructions when demonstrating the current application.

## Section 8 — Verification checklist

- [ ] Dashboard opens directly and the IL&FS fixture reaches the expected band.
- [ ] Evidence cards show specific values and severity filtering works.
- [ ] Graph Explorer renders related-company and director connections.
- [ ] Provenance exposes `FraudSignal` to `TRIGGERED_BY` source relationships.
- [ ] ITC carousel topology and redaction disclosure are visible.
- [ ] Evergreening topology and repayment evidence are visible.
- [ ] Upload preview and overlay analysis work for a supported input.
- [ ] Report generation produces a UUID, timestamp, disclaimer, and evidence excerpt.
- [ ] Health endpoints identify local graph/model availability.
- [ ] Gonka Verification clearly reports when the provider is unavailable or
      when claim text is insufficient for verification.