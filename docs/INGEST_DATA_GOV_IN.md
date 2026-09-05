# data.gov.in bulk MCA master-data ingest — operator runbook

> PRD §10 Phase C free-source plan. Fills the **bulk CIN enumeration**
> gap that neither MCA21 V3 nor the MCA Public Portal scraper can plug.

## Why this exists

Neither MCA21 V3 nor the public-portal Playwright scraper exposes a
"list every registered Indian company" endpoint. Before Phase C,
`CompositeCompanySource.list_available_cins()` returned only the demo
fixture set (~10 companies). That made it impossible to seed a real
"scan all SMEs in Maharashtra registered after 2018" sweep.

data.gov.in publishes annual snapshots of every registered Indian
company as CSV under a CC-BY-style open license at:

  https://www.data.gov.in/catalog/company-master-data

This source loads those CSVs and exposes the full universe.

## How it fits

```
CompositeCompanySource.list_available_cins():
  bulk = DataGovInBulkSource.list_available_cins()    # may be []
  fixture = FixtureSource.list_available_cins()       # always demo-backbone
  return sorted(bulk ∪ fixture)
```

When the operator has not yet run a refresh, `bulk == []` and the
composite behaves exactly as before Phase C — pre-existing call sites
keep working unchanged.

For per-CIN bundles, this source returns a **master-only** bundle
(`company` populated; `directors`, `charges`, `financials` empty). The
snapshot only carries master fields. The composite tries MCA21 V3 →
MCA Public Portal Playwright → fixture first; data.gov.in is the
fall-back when none of those have a real bundle but the CIN is in the
universe.

## Operator setup

```bash
# Cache directory — default ./data/raw/data_gov_in (gitignored)
mkdir -p data/raw/data_gov_in

# Pull the latest annual snapshot from data.gov.in. The catalog page
# links the CSV resource URL — click "Download" → save into the dir.
#   Resource: "Company Master Data"
#   Format:   CSV
#   Size:     ~300-500 MB per annual snapshot (~2.5 M rows)
mv ~/Downloads/CompanyMasterData_*.csv data/raw/data_gov_in/

# Verify the source picks it up
python -c "import asyncio; from backend.app.ingest.data_gov_in import \
  DataGovInBulkSource; print(len(asyncio.run(DataGovInBulkSource().list_available_cins())))"
```

The source memoises the parsed index on first call per process, so the
~500 MB scan only happens once per FastAPI worker start.

## CSV schema accepted

MCA renames columns subtly between snapshots. The parser recognises
these aliases (case-insensitive, ignores underscores / spaces):

| `RawCompany` field | Accepted header tokens |
|---|---|
| `cin` | `CIN`, `Corporate Identification Number`, `CORPORATE_IDENTIFICATION_NUMBER` |
| `name` | `Company Name`, `COMPANY_NAME`, `Name` |
| `incorporation_date` | `Date of Registration`, `DATE_OF_REGISTRATION`, `Incorporation Date`, `Registered Date` |
| `nic_code` | `Industrial Class`, `INDUSTRIAL_CLASS`, `NIC Code`, `Principal Business Activity As Per CIN Class` |
| `state` | `Registered State`, `REGISTERED_STATE`, `State` |
| `registered_address` | `Registered Office Address`, `REGISTERED_OFFICE_ADDRESS`, `Address` |

Date variants accepted: ISO `2005-04-01`, DD-MM-YYYY `01-04-2005`,
DD/MM/YYYY `01/04/2005`, "DD Mon YYYY".

State variants accepted: 2-letter codes as-is (`MH`, `KA`, `DL`); or
full names mapped via `_STATE_CODE` (every Indian state + UT).

NIC variants accepted: `45201`, `"45201 Construction of buildings"` —
the parser extracts the leading integer.

Rows missing CIN, date, NIC, or state are silently dropped (logged at
warning level). The validator in `RawCompany` does the final check.

## What's NOT in this snapshot

| Field on `CompanyBundle` | Source for it |
|---|---|
| `RawCompany.*` | ✅ data.gov.in (this) |
| `RawDirector.*` | MCA21 V3 / MCA Public Portal scraper |
| `RawCharge.*` | MCA Public Portal scraper / CERSAI fixture |
| `RawFinancialStatement.*` | `/upload/financials` PDF (Day-16) |

## What breaks first when MCA changes their CSV

Most likely failure mode is a header rename. Symptom: every row gets
dropped at the "incomplete master" warning. The parser's column-alias
map at the top of
[`backend/app/ingest/data_gov_in.py`](../backend/app/ingest/data_gov_in.py)
(`_COL_ALIASES`) needs the new header token added — the lookup is
case-insensitive and ignores non-alphanumerics, so most renames slot
in with a single entry.

Tests at
[`backend/tests/test_data_gov_in.py`](../backend/tests/test_data_gov_in.py)
pin two distinct header styles (snake_case and "Title Case With Spaces")
so the resilience doesn't silently regress.

## CI safety

Tests inject the CSV via the `csv_texts=[...]` constructor arg — they
never read from disk. The annual snapshot is ~500 MB; we don't check
that into git (`data/raw/` is in `.gitignore`).

## Files in this slice

- [`backend/app/ingest/data_gov_in.py`](../backend/app/ingest/data_gov_in.py) — `DataGovInBulkSource`.
- [`backend/app/ingest/composite.py`](../backend/app/ingest/composite.py) — `list_available_cins()` consults bulk source.
- [`backend/tests/test_data_gov_in.py`](../backend/tests/test_data_gov_in.py) — 10 unit cases.
- This runbook.
