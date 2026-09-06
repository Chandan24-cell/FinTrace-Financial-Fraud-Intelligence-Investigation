# FinTrace — Demo Video Script

> Live working demo. Every on-screen value is returned by the deployed system or
> the local seeded stack. The three fraud surfaces are real app flows: SME loan
> fraud, GST ITC carousel, and bank-loan evergreening.

**Recording target:** 1080p screen capture.
**Length:** 2:50-3:00. Hard cap at 3:00.

## Pre-flight

```bash
python scripts/seed_neo4j.py --clean
```

For a local recording, reseed the local Neo4j/GDS stack so no stale demo data
appears on screen. For the hosted recording, open the deployed dashboard and
use the available fixture-backed data.


```



---

## 0:00-0:18 - Company Search: One Engine, One CIN

> "FinTrace detects financial fraud across SME loans, GST input-tax-credit
> carousels, and bank-loan evergreening. This is the live app, not a slide deck.
> We start with IL&FS, a public-record fraud case with Rs 91,000 crore exposure.
> CIN: U45201MH2005PTC155294."

**On-screen:** `/search`. Paste `U45201MH2005PTC155294`, hit Enter.

**Point to:** the IL&FS demo chip if visible, then the search result route into
the dashboard.

---

## 0:18-0:48 - Analysis Dashboard

> "The score is CRITICAL, with a calibrated fraud probability and a 90 percent
> conformal interval. Data confidence tells us how complete the public-record
> packet is. The source badge shows SFIO, NCLT, and RBI, and every evidence card
> cites structured values from the graph. The LLM narrative is only prose; it is
> not allowed to invent numbers."

**On-screen:** Dashboard for IL&FS.

**Point to, in order:**
- Risk score and CRITICAL band.
- `p_fraud_calibrated` and conformal interval.
- Company metadata row: industry, state, incorporation year.
- Source badge: `SFIO - NCLT - RBI` public-record line.
- Severity filter chips; click `CRITICAL`, then restore all.
- Gonka verification is available from its dedicated view when `GONKA_API_KEY`
  is configured; it analyzes supplied claim text and does not retrieve outside
  evidence.

---

## 0:48-1:18 - Graph Explorer and Evidence Provenance

> "This is the important part: explainability is graph-native. FraudSignal nodes
> connect through TRIGGERED_BY edges to the source records represented in the
> graph. This makes the output useful for structured investigation and review."

**On-screen:** `/graph/U45201MH2005PTC155294`.

**Actions:**
- Hover a FraudSignal node to show the evidence string.
- Click a director or source node to show linked records.
- Click **Export JSON** to download `sentinel-g-<cin>-provenance.json`.

**Narration bridge:** "The dashboard summarizes risk; this screen proves where
that risk came from."

---

## 1:18-1:48 - GST ITC Carousel

> "Now switch fraud type. This is the DGGI Mumbai ITC carousel surface. The page
> reads the seeded ring fixture, draws the CLAIMS_ITC_FROM cycle, and
> runs analysis for every CIN in the ring. Cancelled or missing-trader GST
> entities are highlighted, and the edge labels show invoice value moving around
> the loop."

**On-screen:** `/itc`.

**Point to:**
- Header provenance: DGGI zone, verified date, CBIC press-release archive.
- The `7-node CLAIMS_ITC_FROM cycle` topology.
- Metrics: cycle edges, invoice count, ITC churn.
- Click one GST node; show selected GSTIN turnover, tax paid, and tax/turnover.
- Per-node cards: score, data confidence, signal count, and graph-pattern
  evidence.
- Source disclosure at the bottom: company names redacted, topology preserved.

---

## 1:48-2:18 - DHFL Evergreening

> "Third fraud type: bank-loan evergreening. This DHFL screen is no longer a
> placeholder. It reads the seeded loan-flow graph, shows the FUNDED_REPAYMENT_OF
> chain, and then scores the canonical DHFL CIN from the available graph data. Short-cycle repayments,
> high overlap, and shell routing become graph-pattern evidence."

**On-screen:** `/evergreening`.

**Point to:**
- Badge: `SFIO / RBI public-record pattern - graph fixture active`.
- Topology metrics: companies, round trips, repaid value.
- The DHFL center node and two shell-company loan flows.
- Score panel: fraud risk, source override, info quality, signals.
- Expand a `Source records` detail under graph-pattern evidence.
- Click **Open provenance graph** if time allows.

---

## 2:18-2:50 - Report Export

> "Finally, the same evidence chain becomes a downloadable investigation artifact.
> The PDF is stamped with a report ID and UTC timestamp, and includes
> the score, confidence, interval, evidence chain, source disclaimer, and audit trail. It can be retained with the case documentation."

**On-screen:** `/reports`.

**Actions:**
- Enter `U45201MH2005PTC155294`.
- Click **Download PDF**.
- Open the PDF if it appears quickly; otherwise point to the browser download.

**Point to in PDF:** report ID, generated timestamp, disclaimer, score, evidence
chain excerpt.

---

## 2:50-3:00 - Close

> "FinTrace is one graph engine across three fraud classes: SME loan fraud,
> GST ITC carousel fraud, and bank-loan evergreening. The model gives a risk
> score; the graph gives the evidence trail."

**On-screen:** Return to dashboard or keep the report visible.

---

## Numbers to Rehearse

| Surface | Identifier | Expected live result |
|---|---|---|
| IL&FS dashboard | `U45201MH2005PTC155294` | CRITICAL, public-record source badge, full evidence chain |
| IL&FS provenance | `/graph/U45201MH2005PTC155294` | FraudSignal nodes with TRIGGERED_BY records and JSON export |
| ITC carousel | `/itc` / `DGGI-MZU-2024-Q1` | Seeded GST entities, invoice edges, per-CIN cards |
| DHFL evergreening | `L65910MH1984PLC032662` | CRITICAL fixture, FUNDED_REPAYMENT_OF topology, source records |
| Report export | `/reports` | PDF with UUID, UTC timestamp, evidence excerpt |

---

## Timing Guardrails

| Segment | Max time |
|---|---:|
| Search | 0:18 |
| Dashboard | 0:30 |
| Provenance graph/export | 0:30 |
| ITC carousel | 0:30 |
| DHFL evergreening | 0:30 |
| PDF report | 0:32 |
| Close | 0:10 |

If the browser or PDF viewer is slow, skip opening the PDF and end on the report
download confirmation. Do not cut the ITC or DHFL segments; those are the newly
real surfaces judges need to see.

---

## After Recording

- Trim head/tail to <= 3:00.
- Export H.264 1080p, <= 50 MB for submission.
