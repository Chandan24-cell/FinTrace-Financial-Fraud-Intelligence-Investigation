# Sentinel-G — Persona Walkthrough Guide

> A scripted end-to-end tour of every feature available to every role.
> Use this to verify the running app the way a fraud-desk team would
> actually use it — and to spot any feature you've shipped but never
> manually tested.

If you've never run the app before, finish `docs/RUNNING.md` first
(Neo4j + uvicorn + Vite all up). Then come back here.

---

## Section 0 — One-time setup

### 0.1 Seed the demo users

```powershell
.venv\Scripts\python.exe scripts/seed_users.py
```

Idempotent. Creates four `:User` nodes in Neo4j, one per role. If you've
run it before it just prints `Skipped … (already exists)` and moves on.

### 0.2 (Optional) Seed the Tamil Nadu universe

If you want `/search` to find real Indian SMEs (not just the curated
demo CINs), drop `TN_Companies_Master_Data.csv` (or any data.gov.in MCA
master CSV) into `data/raw/data_gov_in/` and run:

```powershell
.venv\Scripts\python.exe scripts/seed_data_gov_in.py --limit 200000
```

That seeds ~191,531 TN-registered companies as `:Company` master nodes.
See `docs/INGEST_DATA_GOV_IN.md` for the operator runbook.

### 0.3 Credentials

All four demo users share the password `Sentinel@1`.

| Email             | Password    | Role           | Persona                                        |
|-------------------|-------------|----------------|------------------------------------------------|
| `priya@demo.in`   | `Sentinel@1`| credit_officer | Priya Sharma — SBI Loan Officer                |
| `rajan@demo.in`   | `Sentinel@1`| investigator   | Rajan Mehta — DGGI (GST Intelligence) Inspector|
| `deepa@demo.in`   | `Sentinel@1`| auditor        | Deepa Krishnan — NCLT Resolution Professional  |
| `amir@demo.in`    | `Sentinel@1`| admin          | Amir Khan — Compliance & Platform Admin        |

> **Reset note**: passwords are bcrypt-hashed in Neo4j. To rotate, edit
> `scripts/seed_users.py` and re-run; for one-off password changes,
> drop the `:User` node and re-seed.

### 0.4 Test CINs you'll use throughout this guide

**SFIO-confirmed fraud cases (all resolve CRITICAL via M9 NCLT/wilful-defaulter override):**

| CIN                       | Company              | Expected band | Notes                                              |
|---------------------------|----------------------|---------------|----------------------------------------------------|
| `U45201MH2005PTC155294`   | IL&FS                | CRITICAL      | SFIO-confirmed; 18-signal chain; green badge       |
| `L65910MH1984PLC032662`   | DHFL                 | CRITICAL      | Evergreening page; patterns 13/14/15 fire together |
| `U27101MH2010PTC215432`   | Amtek Auto           | CRITICAL      | NCLT CIRP + wilful defaulter; green badge          |
| `L45200GJ1988PLC011533`   | Bhushan Steel        | CRITICAL      | NCLT Delhi 2017; ₹56,000 cr                        |
| `L99999MH1992PLC066213`   | Jet Airways          | CRITICAL      | NCLT Mumbai 2019; CIRP                             |
| `L74110WB1933PLC008002`   | Kingfisher Airlines  | CRITICAL      | SBI fraud 2016; airline grounded 2012              |
| `L45203AP1993PLC014991`   | Lanco Infratech      | CRITICAL      | NCLT Hyderabad 2017; ₹45,000 cr                    |
| `L22219MH1985PLC036572`   | Videocon Industries  | CRITICAL      | NCLT Mumbai 2020; ICICI/Chanda Kochhar angle       |
| `L21300MH1996PLC102369`   | HDIL                 | CRITICAL      | PMC Bank ₹6,500 cr hidden exposure                 |
| `L99999GJ1995PLC026167`   | Gitanjali Gems       | CRITICAL      | PNB LoU fraud; Mehul Choksi                        |
| `U24232GJ1995PLC025935`   | Sterling Biotech     | CRITICAL      | NCLT Ahmedabad; Sandesara brothers                 |
| `L23209UP1989PLC020525`   | RCom                 | CRITICAL      | Anil Ambani; ₹47,000 cr default                    |
| `U99999MH1996PLC163336`   | Solar Exports        | CRITICAL      | Nirav Modi PNB LoU ₹13,578 cr; FEO declared        |
| `U74899DL2001PTC108944`   | ABG Shipyard         | CRITICAL      | Largest bank fraud case (CBI 2022); ₹22,842 cr     |

**ITC Carousel synthetic ring (DGGI Mumbai topology, company names redacted):**

| CIN                       | Role in ring         | Expected band |
|---------------------------|----------------------|---------------|
| `U27109MH2018PTC312456`   | Node A — Issuer      | CRITICAL      |
| `U46101MH2017PTC289123`   | Node B — Recipient   | CRITICAL      |
| `U46190MH2019PTC295432`   | Node C — Conduit     | CRITICAL      |

**Clean non-fraud controls (LOW band, no M9 overrides):**

| CIN                       | Company              | Expected band | Notes                                  |
|---------------------------|----------------------|---------------|----------------------------------------|
| `L85110KA1981PLC013115`   | Infosys              | LOW           | No signals fire                        |
| `L15140MH1988PLC049208`   | Marico               | LOW           | No signals fire                        |
| `L24201MH1945PLC004598`   | Asian Paints         | LOW           | No signals fire                        |
| `L74999TN1984PLC010444`   | Titan                | LOW           | No signals fire                        |
| `L18101KA1994PLC016910`   | Page Industries      | LOW           | No signals fire                        |
| `U10719TN2026PTC192803`   | Cherry Gourmet (TN)  | LOW           | Master-only record; `data_confidence=25` |

---

## Section 1 — Priya, SBI Credit Officer (`credit_officer`)

**Scenario:** A loan application lands on Priya's desk for a Tamil Nadu
SME. She has 10 minutes to decide if it goes to her senior with a
"proceed" or "stop" stamp. She's the **first line of defence**.

### 1.1 Log in

1. Navigate to `http://localhost:5173/login`
2. Email `priya@demo.in`, password `Sentinel@1`
3. **Expect:** redirect to `/search`. Top-nav shows her email + a
   "Log out" button. The Reports link is visible (clicking it triggers
   a 403 — see §1.10 below).

### 1.2 Quick screen on a known-bad CIN

1. On the Search page, paste `U45201MH2005PTC155294` (IL&FS) into the
   CIN field and press Enter.
2. **Expect:** route to `/dashboard?cin=U45201MH2005PTC155294`.
   - Risk band: **CRITICAL** (red)
   - `fraud_risk_score`: ~75
   - `data_confidence`: ~92
   - Company metadata row: **Construction (NIC 45201)** · State MH — human-readable industry name, not raw code
   - Evidence chain ≥ 10 `FraudSignal` rows, each citing **specific numbers** (₹ amounts, ratios). No generic prose.
   - Module breakdown shows non-zero contributions from M01 Beneish, M02 Cross-Statement, M09 NCLT/Wilful.

### 1.3 Verify the source badge

1. On the IL&FS Dashboard, look below the `CRITICAL` band stamp.
2. **Expect:** a small green dot followed by **"SFIO · NCLT · RBI — public court record"** in eyebrow font.
3. Switch to Amtek Auto (`U27101MH2010PTC215432`) — same green badge.
4. Navigate to `/evergreening` (DHFL) — grey badge: **"SFIO / RBI public-record pattern - graph fixture active"**.
5. Navigate to `/itc` — amber badge: **"DGGI Mumbai · company names redacted"**.

### 1.4 Use the severity filter chips

1. Back on the IL&FS Dashboard, locate the pill row between the ScorePlate and the evidence chain.
   It shows coloured chips: `● N CRITICAL  ● N HIGH  ● N MEDIUM  ● N LOW`.
2. Click the **CRITICAL** chip.
3. **Expect:** evidence chain narrows to only CRITICAL-severity signals. Pill row now shows an **✕ show all** chip.
4. Click **✕ show all** → all signals restored.
5. Switch to Amtek Auto — chip counts will differ from IL&FS (confirms it's live, not hardcoded).

### 1.5 Inspect the evidence graph

1. Click the **Graph** tab in the nav, or navigate to
   `/graph/U45201MH2005PTC155294`.
2. **Expect:** D3 force-layout with the IL&FS node at centre, director
   nodes orbiting, shared-director edges to related companies.
   Labels render only after the simulation settles (`sim.alpha() < 0.05`).
3. Hover any FraudSignal node to see its `TRIGGERED_BY` provenance.

### 1.6 GST carousel screen

1. Navigate to `/itc`.
2. **Expect:** the ring SVG diagram at the **top of the page** (above the company cards).
   - Triangle A→B→C→A with arrowhead markers, ₹512 cr label at the centre.
   - Amber source badge below the page title: "DGGI Mumbai · company names redacted"
3. Below the diagram: three carousel cards (Nodes A/B/C) each showing CRITICAL band.

### 1.7 Evergreening view (DHFL)

1. Navigate to `/evergreening`.
2. **Expect:** a shimmer skeleton animates briefly during load (not a plain text paragraph).
3. After load:
   - Grey source badge: "SFIO / RBI public-record pattern - graph fixture active"
   - DHFL's 4-column metrics grid: fraud risk · override reason · info quality · signals
   - Evidence list with specific ₹ amounts

### 1.8 Honest TN bulk lookup (real public registry, low confidence)

1. Back to `/search`. Scroll to the **Tamil Nadu · live MCA registry** panel.
2. Click any row (e.g. Cherry Gourmet Private Limited).
3. **Expect:** `/dashboard?cin=U10719TN2026PTC192803`.
   - Risk band: **LOW** (no flags fired)
   - `fraud_risk_score`: 0.0
   - `data_confidence`: 25 — **honestly low**, because only a master record exists (no financials, no directors).
   - Skipped modules list shows reasons like `"No financials"` and `"Need 2+ consecutive FS years"`.

### 1.9 Upload to enrich a thin file

1. Navigate to `/upload`.
2. **Expect:** upload form. As `credit_officer`, Priya CAN POST to
   `/upload/financials`, `/upload/gst`, `/upload/bank`.
3. Drop a PDF financial statement (sample at `data/synthetic_pdfs/sample_aoc4.pdf` if available).
4. **Expect:** upload preview card showing parsed rows, then "Accepted".
5. Re-run `/dashboard?cin=<that CIN>` — `data_confidence` should now be visibly higher.

### 1.10 What Priya CAN'T do

1. Click **Reports** in the nav (or navigate directly to `/reports`, then try the Download PDF button).
2. **Expect:** `HTTP 403 — Role 'credit_officer' is not authorised for this endpoint. Required one of: ['admin', 'auditor', 'investigator']`.
   The Reports page renders this error inline — no crash, no redirect.

### 1.11 Logout

Click **Log out** in the nav. Token cleared from localStorage. Land back on `/login`.

### Priya's verification checklist

- [ ] Login as `priya@demo.in` works
- [ ] `/search` reachable; demo CIN chips clickable
- [ ] TN bulk panel renders (if Section 0.2 was run)
- [ ] Dashboard for IL&FS: CRITICAL band, score 75, evidence chain with ₹-specific strings
- [ ] Company metadata row shows industry name format: **Construction (NIC 45201)**
- [ ] Green source badge "SFIO · NCLT · RBI — public court record" visible for IL&FS + Amtek
- [ ] Grey badge "SFIO / RBI public-record pattern - graph fixture active" on `/evergreening`
- [ ] Amber badge "DGGI Mumbai · company names redacted" on `/itc`
- [ ] Severity filter chips render; clicking CRITICAL narrows the chain; "✕ show all" restores
- [ ] Chip counts differ between IL&FS and Amtek Auto
- [ ] `/graph` D3 force layout renders without label overlap
- [ ] `/itc`: ring SVG diagram appears *above* the three company cards
- [ ] `/evergreening`: shimmer skeleton shows during load; 4-column grid after load
- [ ] `/upload` accepts a PDF and reflects the overlay in next `/analyse`
- [ ] `/reports` returns 403 with the role-mismatch detail string

---

## Section 2 — Rajan, DGGI Inspector (`investigator`)

**Scenario:** Rajan investigates GST input-tax-credit carousels for the
Directorate General of GST Intelligence. He has the broadest access in
the app — upload, analyse, report. A whistleblower has tipped him off
about a Mumbai-based ring, and he has 30 minutes to compile evidence
before a 4 PM raid briefing.

### 2.1 Log in

1. Log out from Priya's session if you're still in it.
2. Login as `rajan@demo.in` / `Sentinel@1`.
3. **Expect:** `/search`. The Reports link is now functional.

### 2.2 Start with the GST carousel page

1. Navigate to `/itc`.
2. **Expect:**
   - At the **top**: the ring SVG — triangle A→B→C→A, ₹512 cr at centre, arrowhead markers on each edge.
   - Amber source badge: "DGGI Mumbai · company names redacted"
   - Below the diagram: three carousel cards (Nodes A/B/C), all CRITICAL band.
3. Company names are redacted because the underlying DGGI case is under active investigation.

### 2.3 Multi-hop director graph

1. Navigate to `/graph?cin=U45201MH2005PTC155294`.
2. Use the depth selector (default 3, max 5) to fan out the director chain.
3. **Expect:** at depth 3, ~15-25 connected `:Company` nodes via `IS_DIRECTOR_OF` edges.
4. Click any director node to highlight all companies they're attached to.

### 2.4 Verify NIC labels across companies

1. `/dashboard?cin=L45200GJ1988PLC011533` (Bhushan Steel)
   - **Expect:** **Primary Metal Manufacturing (NIC 27209)** · State GJ
2. `/dashboard?cin=L99999MH1992PLC066213` (Jet Airways)
   - **Expect:** **Air Transport (NIC 62200)** · State MH
3. `/dashboard?cin=L85110KA1981PLC013115` (Infosys — clean control)
   - **Expect:** **Software (NIC 85110)** · State KA · LOW band

### 2.5 Upload supplementary evidence

1. Navigate to `/upload`.
2. Try uploading a GST payload:
   ```json
   { "gstin": "27ABCDE1234F1Z5", "aggregate_turnover": 22000000,
     "tax_paid_ytd": 180000 }
   ```
3. **Expect:** 200 OK with `{"cin": "...", "accepted": true, ...}`.
4. The next `/analyse/{cin}` call folds this overlay onto the bundle.

### 2.6 Export the evidence dossier

1. Navigate to `/reports`.
2. In the CIN field type `U45201MH2005PTC155294` (IL&FS) and click **Download PDF**.
3. **Expect:** `.pdf` named `sentinel-g-U45201MH2005PTC155294-<8-char-uuid>.pdf`.
4. Open the PDF — verify:
   - `Report ID: <UUID v4>` and `Generated: <UTC ISO timestamp>` on cover
   - Disclaimer paragraph naming MCA21, CERSAI, NCLT as inputs
   - Truncated evidence chain (top-N FraudSignals)
5. The exported row appears in the "Recently exported" table.

### 2.7 Test additional fraud CINs

```
L45200GJ1988PLC011533   Bhushan Steel   → CRITICAL, override_applied: true
U24232GJ1995PLC025935   Sterling Biotech → CRITICAL, override_applied: true
L24201MH1945PLC004598   Asian Paints    → LOW,      override_applied: false, score: 0.0
```

### 2.8 Narrative endpoint smoke test

1. Open browser DevTools → Network tab.
2. From any Dashboard page, watch for `GET /narrative/{cin}`.
3. **Expect:** 200 OK with a `summary` string grounded in real evidence numbers.

### Rajan's verification checklist

- [ ] Login as `rajan@demo.in` works
- [ ] All Priya checks pass for Rajan too
- [ ] `/itc`: ring SVG at top; amber source badge present
- [ ] NIC label format correct on at least 3 different companies
- [ ] `/upload/financials`, `/upload/gst`, `/upload/bank` — all 200 OK
- [ ] `/reports` Download PDF works (UUID + UTC + disclaimer)
- [ ] Recently Exported table records each download
- [ ] Bhushan Steel + Sterling Biotech → CRITICAL + override_applied: true
- [ ] Asian Paints → LOW + override_applied: false
- [ ] `/narrative` returns a non-empty summary

---

## Section 3 — Deepa, NCLT Resolution Professional (`auditor`)

**Scenario:** Deepa is the court-appointed RP on a CIRP case for DHFL.
She's preparing the information memorandum for the Committee of Creditors
meeting tomorrow. She needs the audit trail, the exported PDF dossier, and
the evidence chain — but she does NOT need (and should NOT have) write access.

### 3.1 Log in

1. Logout, then login as `deepa@demo.in` / `Sentinel@1`.

### 3.2 DHFL evergreening review

1. Navigate to `/evergreening`.
2. **Expect:**
   - Shimmer skeleton animates briefly during load.
   - After load: grey source badge "SFIO / RBI public-record pattern - graph fixture active" appears.
   - 4-column metrics grid: fraud risk · override reason · info quality · signals.
3. Pattern 13 (loan round-tripping), 14 (overlapping timestamps), 15 (related-party intermediary) all fire.
4. Each signal cites specific ₹ amounts.

### 3.3 Confirm the dual-output payload

1. Navigate to `/dashboard?cin=L65910MH1984PLC032662`.
2. **Expect:**
   - Band: CRITICAL
   - `fraud_risk_score`: ~78
   - `p_fraud_calibrated`: a real number (the meta-learner is wired)
   - `p_fraud_interval`: a 90% conformal interval, e.g. `[0.74, 0.95]`
   - `data_confidence`: 88-92
   - `override_applied: true`
3. Severity filter chips render — click each to confirm they narrow the evidence chain.

### 3.4 Export the audit-ready dossier

1. Navigate to `/reports`.
2. **Expect:** Reports page renders (Deepa has the `auditor` role).
3. Download DHFL's PDF — verify UUID + timestamp + disclaimer + evidence chain.

### 3.5 Provenance traversal (for the IM)

1. Open DevTools, navigate to `/dashboard?cin=L65910MH1984PLC032662`.
2. Watch the network tab for `GET /analyse/{cin}/provenance`.
3. **Expect:** 200 OK with:
   ```json
   { "cin": "L65910MH1984PLC032662",
     "signal_count": 12,
     "signals": [ ... ],
     "triggered_by": [
       { "signal_id": "...", "label": "FinancialStatement", "ref": {...} }
     ]
   }
   ```

### 3.6 What Deepa CAN'T do

1. Navigate to `/upload` and try submitting.
2. **Expect:** `HTTP 403 — Role 'auditor' is not authorised for this endpoint. Required one of: ['admin', 'credit_officer', 'investigator']`.

### Deepa's verification checklist

- [ ] Login as `deepa@demo.in` works
- [ ] `/evergreening`: shimmer skeleton on load; grey badge after load
- [ ] DHFL patterns 13/14/15 all appear in the evidence list
- [ ] `/dashboard` shows `override_applied: true` for DHFL
- [ ] `p_fraud_calibrated` and `p_fraud_interval` populate (not null)
- [ ] Severity filter chips work on DHFL dashboard
- [ ] `/reports` Download PDF works
- [ ] `/provenance` returns signal_count > 0 with `triggered_by` chain
- [ ] `/upload/financials`, `/upload/gst`, `/upload/bank` all return 403

---

## Section 4 — Amir, Compliance & Platform Admin (`admin`)

**Scenario:** Amir owns the platform. His weekly job is to (a) verify
the system is healthy, (b) spot-check overrides for false positives,
(c) review user registrations and assign roles.

### 4.1 Log in

1. Logout, then login as `amir@demo.in` / `Sentinel@1`.

### 4.2 Health checks

Open three browser tabs:

1. `http://localhost:8000/health` → `{"status":"ok","version":"0.1.0","env":"dev"}`
2. `http://localhost:8000/health/ml` → `ok: true` means F1a/F1b/F1c are loaded.
3. `http://localhost:8000/health/neo4j` → `{"neo4j_reachable": true, "gds_version": "2.x.x"}`

### 4.3 Full report sweep

1. Navigate to `/reports`.
2. Generate reports for: IL&FS, DHFL, Amtek Auto, Bhushan Steel, ABG Shipyard.
3. **Expect:** all download successfully.

### 4.4 Override audit

1. Navigate to `/dashboard?cin=U45201MH2005PTC155294`.
2. Open DevTools network tab → `GET /analyse/U45201MH2005PTC155294`.
3. **Expect:** `override_applied: true`, `fraud_risk_score ≥ 75` (NCLT/WD floor per PRD §7.3).
4. Repeat for `L85110KA1981PLC013115` (Infosys): `override_applied: false`, score 0.0, band LOW.

### 4.5 Companies directory

1. `http://localhost:8000/companies?state=TN&limit=50` (with Amir's JWT in the `Authorization` header).
2. **Expect:** `{"total": <number>, "items": [...]}`. After §0.2, `total` should be ~191,531.

### 4.6 User management (direct Cypher)

```powershell
docker exec sentinel-g-neo4j cypher-shell -u neo4j -p sentinel_dev_pwd `
  "MATCH (u:User) RETURN u.email, u.role, u.is_active ORDER BY u.email"
```

To promote a self-registered user to admin:

```powershell
docker exec sentinel-g-neo4j cypher-shell -u neo4j -p sentinel_dev_pwd `
  "MATCH (u:User {email: 'someone@example.com'}) SET u.role = 'admin' RETURN u.email, u.role"
```

> The user must log out and log back in for the new role claim to appear in their JWT.

### Amir's verification checklist

- [ ] Login as `amir@demo.in` works
- [ ] `/health`, `/health/ml`, `/health/neo4j` all return 200
- [ ] All fraud demo CIN reports download
- [ ] `override_applied: true` for IL&FS + DHFL + Bhushan Steel; `false` for Infosys
- [ ] `/companies?state=TN` returns rows
- [ ] cypher-shell can query and update `:User` nodes
- [ ] No 403 errors anywhere — admin has full access

---

## Section 5 — External evaluator / judge walkthrough (5 minutes)

For a hackathon judge who wants to see every feature in one continuous path.

| Time | Action | What to verify |
|------|--------|----------------|
| 0:00 – 0:20 | Login as `priya@demo.in`; search `U45201MH2005PTC155294` | Search → CRITICAL band in < 1 sec |
| 0:20 – 0:35 | Point to company metadata row | **Construction (NIC 45201)** — human-readable NIC label |
| 0:35 – 0:50 | Point to source badge below band stamp | Green dot · "SFIO · NCLT · RBI — public court record" |
| 0:50 – 1:05 | Click CRITICAL severity chip; click ✕ show all | Filter chips work; chain narrows and restores |
| 1:05 – 1:20 | Point to `p_fraud_calibrated` + `p_fraud_interval` in aside | PRD §7.1 dual-output live, not stubbed |
| 1:20 – 1:40 | Click Graph tab; expand to depth 3 | Director chain, NCLT proceedings, wilful-defaulter flags as edges |
| 1:40 – 1:55 | Hover FraudSignal → show TRIGGERED_BY chain | Every flag has provenance to source numbers |
| 1:55 – 2:15 | Switch to `/itc`; point to ring SVG, then amber badge | A→B→C→A ring diagram above the cards; DGGI provenance disclosed |
| 2:15 – 2:35 | Switch to `/evergreening`; show skeleton fade, grey badge, 4-metric grid | Shimmer load state; SFIO-pattern source disclosed |
| 2:35 – 2:55 | Logout; login as `rajan@demo.in`; `/reports`; download IL&FS PDF | Investigator role unlocks report export |
| 2:55 – 3:15 | Open PDF; point to UUID + UTC + disclaimer | Audit trail is real, court-admissible format |
| 3:15 – 3:40 | Back to `/upload`; upload a synthetic financial PDF | Live overlay — uploaded data folds into next `/analyse` |
| 3:40 – 4:00 | Re-run `/dashboard`; show data_confidence increased | Upload pipeline works end-to-end |
| 4:00 – 4:20 | Logout; login as `deepa@demo.in`; try `/upload` | 403 with role-mismatch detail — RBAC enforces read-only for auditors |
| 4:20 – 4:40 | Navigate to `/evergreening`; show grey badge, metrics grid | Deepa can read and export, cannot modify |
| 4:40 – 5:00 | Logout; login as `amir@demo.in`; visit `/health/neo4j` | Platform health surface for admin oversight |

---

## Section 6 — Feature × role matrix

Source of truth: `require_roles()` decorators in
[`backend/app/api/upload.py:34`](../backend/app/api/upload.py#L34) and
[`backend/app/api/report.py:181`](../backend/app/api/report.py#L181).

| Feature                        | Endpoint                              | Priya (credit_officer) | Rajan (investigator) | Deepa (auditor) | Amir (admin) |
|--------------------------------|---------------------------------------|------------------------|----------------------|-----------------|--------------|
| Login                          | `POST /auth/login`                    | ✅                     | ✅                   | ✅              | ✅           |
| Search page                    | (frontend route)                      | ✅                     | ✅                   | ✅              | ✅           |
| Analyse CIN                    | `GET /analyse/{cin}`                  | ✅                     | ✅                   | ✅              | ✅           |
| Provenance traversal           | `GET /analyse/{cin}/provenance`       | ✅                     | ✅                   | ✅              | ✅           |
| Narrative summary              | `GET /narrative/{cin}`                | ✅                     | ✅                   | ✅              | ✅           |
| Companies directory            | `GET /companies?state=...`            | ✅                     | ✅                   | ✅              | ✅           |
| Upload preview                 | `GET /upload/{cin}/preview`           | ✅                     | ✅                   | ❌ 403          | ✅           |
| Upload financials PDF          | `POST /upload/financials/{cin}`       | ✅                     | ✅                   | ❌ 403          | ✅           |
| Upload GST payload             | `POST /upload/gst/{cin}`              | ✅                     | ✅                   | ❌ 403          | ✅           |
| Upload bank credits            | `POST /upload/bank/{cin}`             | ✅                     | ✅                   | ❌ 403          | ✅           |
| Export report PDF              | `GET /report/{cin}`                   | ❌ 403                 | ✅                   | ✅              | ✅           |
| Health: app                    | `GET /health`                         | ✅ (no auth)           | ✅                   | ✅              | ✅           |
| Health: ML stack               | `GET /health/ml`                      | ✅ (no auth)           | ✅                   | ✅              | ✅           |
| Health: Neo4j + GDS            | `GET /health/neo4j`                   | ✅ (no auth)           | ✅                   | ✅              | ✅           |

**403 expectations:** every `❌ 403` cell returns:
`{"detail": "Role '<role>' is not authorised for this endpoint. Required one of: [...]"}`.
Not 401, not 500. A different code = regression.

---

## Section 7 — What's NOT covered by this walkthrough

- **Live MCA Public Portal scraping** — requires `python -m backend.app.ingest.mca_public_playwright --bootstrap` to bank session cookies. Without it, non-seeded CINs return 404. See `docs/INGEST_MCA_PUBLIC.md`.
- **Mistral narrative** — runs in template-fallback mode in dev (no `MISTRAL_API_KEY`). Still grounded in real numbers. Set a real Mistral API key in `.env.local` to flip it on.
- **NCLT / RBI Wilful Defaulter live polling** — scrapers are coded (`ibbi_fetcher.py`, `rbi_fetcher.py`, `gstn_fetcher.py`, `nclt.py`) but run against fixture sources in dev.
- **Production deployment** — see `docs/DEPLOY_ORACLE.md` / `DEPLOY_MILESWEB.md` / `DEPLOY_ORACLE_AMPERE.md`.

---

## Section 8 — Quick reference card

```
ROLES
  Priya   credit_officer   priya@demo.in    Sentinel@1   upload YES   report NO
  Rajan   investigator     rajan@demo.in    Sentinel@1   upload YES   report YES
  Deepa   auditor          deepa@demo.in    Sentinel@1   upload NO    report YES
  Amir    admin            amir@demo.in     Sentinel@1   upload YES   report YES

SFIO FRAUD CASES (all CRITICAL — NCLT override active)
  IL&FS            U45201MH2005PTC155294   Construction MH
  DHFL             L65910MH1984PLC032662   Finance MH       (evergreening page)
  Amtek Auto       U27101MH2010PTC215432   Metal Products MH
  Bhushan Steel    L45200GJ1988PLC011533   Primary Metal GJ
  Jet Airways      L99999MH1992PLC066213   Air Transport MH
  Kingfisher       L74110WB1933PLC008002   Air Transport WB
  Lanco Infratech  L45203AP1993PLC014991   Construction AP
  Videocon         L22219MH1985PLC036572   Electronics MH
  HDIL             L21300MH1996PLC102369   Real Estate MH
  Gitanjali Gems   L99999GJ1995PLC026167   Jewellery GJ
  Sterling Biotech U24232GJ1995PLC025935   Pharmaceuticals GJ
  RCom             L23209UP1989PLC020525   Telecom UP
  Solar/Nirav Modi U99999MH1996PLC163336   Gems MH
  ABG Shipyard     U74899DL2001PTC108944   Shipbuilding DL

ITC CAROUSEL (CRITICAL — DGGI Mumbai topology, names redacted)
  Node A — Issuer    U27109MH2018PTC312456
  Node B — Recipient U46101MH2017PTC289123
  Node C — Conduit   U46190MH2019PTC295432

CLEAN CONTROLS (LOW band, no overrides)
  Infosys       L85110KA1981PLC013115   Software KA
  Marico        L15140MH1988PLC049208   FMCG MH
  Asian Paints  L24201MH1945PLC004598   Paints MH
  Titan         L74999TN1984PLC010444   Watches TN
  Page Ind.     L18101KA1994PLC016910   Apparel KA

UI FEATURES TO VERIFY PER PAGE
  Dashboard → NIC label     "Industry Name (NIC XXXXX)" in metadata row
  Dashboard → source badge  Green = SFIO/real · Grey = synthetic · Amber = topology
  Dashboard → severity chips Coloured pills; click to filter; ✕ to restore
  /itc       → ring SVG     Triangle diagram ABOVE the 3 company cards
  /evergreening → skeleton  Shimmer animation during load, not plain text
```
