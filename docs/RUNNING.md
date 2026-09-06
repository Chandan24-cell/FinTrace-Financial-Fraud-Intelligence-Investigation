# Running FinTrace locally

Everything you need to bring the app up from a fresh `git clone` to a working browser session at `http://localhost:5173`.

If you already have the prereqs installed and `.env.local` filled in, skip to **[Daily run](#3-daily-run)** — it's three commands in three terminals.

---

## 1 · Prerequisites

| Tool | Version | Why |
|---|---|---|
| **Docker Desktop** | latest | Runs Neo4j 5.20 Community + GDS plugin locally (`infra/docker-compose.dev.yml`) |
| **Python** | **3.11.x** (not 3.12+, not 3.14) | torch / torch-geometric / lightgbm wheels are 3.11-only on Windows. `pyproject.toml` pins `requires-python = ">=3.11,<3.12"` |
| **uv** | ≥ 0.5 | Python env + lockfile manager (`pip install uv` or `winget install astral-sh.uv`) |
| **Node.js** | 20 LTS or 24 LTS | Vite 5 + React 18 build |
| **Git** | any recent | source control |

Verify:

```bash
docker --version            # any 24+
uv --version
python --version            # 3.11.x — uv handles this if missing
node --version              # v20.x or v24.x
```

---

## 2 · First-time setup

Run these **once** after cloning.

### 2.1 — Get the code

```bash
git clone https://github.com/Chandan24-cell/FinTrace-Financial-Fraud-Intelligence-Investigation.git
cd FinTrace-Financial-Fraud-Intelligence-Investigation
```

### 2.2 — Create `.env.local`

```bash
cp .env.example .env.local
```

Then edit `.env.local` and set at least:

```env
# Neo4j password — must match the value docker-compose.dev.yml passes to the container.
# Default is `sentinel_dev_pwd` (defined at infra/docker-compose.dev.yml:15).
NEO4J_PASSWORD=sentinel_dev_pwd

# Dev rate limit — production default is 60/min/IP. Setting to 0 picks up the
# per-env default (200/min in dev). Set higher if you're hammering the API.
RATE_LIMIT_PER_MIN=2000
```

Set `GONKA_API_KEY` when testing the GonkaRouter claim-verification view. Other
optional source-integration keys can remain placeholders during local development.

### 2.3 — Start Neo4j

```bash
docker compose -f infra/docker-compose.dev.yml up -d
```

Wait ~20 s for the healthcheck to pass:

```bash
docker ps --filter name=sentinel-g-neo4j --format "{{.Status}}"
# expect: Up 30 seconds (healthy)
```

Neo4j Browser is now at http://localhost:7474 (user `neo4j`, password from `.env.local`).

### 2.4 — Install Python deps

```bash
uv sync --extra dev
```

This creates `.venv/` with Python 3.11, installs the base + `[dev]` extras (pytest, ruff, etc.), and pins everything against `uv.lock`. First run pulls ~2 GB of wheels (torch, lightgbm) and takes 3–5 min.

> **Windows note**: if `uv sync` fails on `pdftopng` (`only has wheels for linux/macos`), append `--no-install-package pdftopng`. That package is only used by an optional OCR path; the app boots without it.

### 2.5 — Install frontend deps

```bash
cd frontend
npm install
cd ..
```

### 2.6 — (Optional) Seed Neo4j with demo data

The app loads its 200 demo companies + 6 ITC carousel rings + DHFL evergreening cluster + NCLT + wilful-defaulter fixtures into Neo4j at backend startup if the graph is empty. No manual seed step is required for normal use.

If you want to *force* a clean reseed:

```bash
uv run python scripts/seed_neo4j.py --clean
```

(Drops every node + relationship, then loads fresh. Takes ~30 s on the 200-company cache.)

---

## 3 · Daily run

Three processes in three terminals. Order matters — Neo4j first, backend second, frontend third.

### Terminal 1 — Neo4j (skip if already running)

```bash
docker compose -f infra/docker-compose.dev.yml up -d
```

`-d` runs detached; once healthy, you can close this terminal.

### Terminal 2 — Backend (FastAPI + uvicorn)

```bash
uv run uvicorn backend.app.main:app --reload --port 8000 --host 0.0.0.0
```

Wait for `Application startup complete.` in the log. The lifespan hook also pre-warms the analytics cache (~7 s) — first request after that is fast.

Verify:

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.1.0","env":"dev"}

curl http://localhost:8000/health/ml
# {"ok":true, "meta_learner":{"loaded":true, "feature_width":45, ...}, ...}
```

If `/health/ml` shows `meta_learner.loaded: false`, see **[Troubleshooting → meta-learner null](#meta-learner-returns-null-p_fraud_calibrated-p_fraud_interval)** below.

### Terminal 3 — Frontend (Vite dev server)

```bash
cd frontend
npm run dev
```

Open the URL it prints — usually http://localhost:5173 (Vite will pick 5174, 5175, … if 5173 is taken).

---

## 4 · Application access and production path

The current deployed application does not require the previous JWT-based
user-registration and role workflow described in older documentation.

Start the backend and frontend as described above, then use the application
directly through the dashboard.

For the current production deployment, the request path is:

Browser → Vercel frontend → Railway FastAPI backend → GonkaRouter

The production Railway environment does not provide the local Neo4j/GDS
development stack, so graph-dependent functionality may operate in degraded
fixture mode.

## 5 · Verifying it actually works

Click around the dashboard:

1. **Search** → enter `U45201MH2005PTC155294` (IL&FS, a confirmed fraud) → click Analyse. You should land on a Dashboard with score **75/100 CRITICAL** and a populated evidence chain (PRD §7.3 override forces the floor).
2. **Dashboard UI** → company metadata row shows **Construction (NIC 45201)** — human-readable industry name. Below the band stamp, the source badge identifies the available public-record or fixture inputs. Severity filter chips (CRITICAL / HIGH / MEDIUM / LOW) appear between the ScorePlate and evidence chain; clicking one narrows the chain.
3. **Graph Explorer** → click any signal node → the inspector rail on the right shows the exact `evidence_string` with ₹-numbers.
4. **ITC Carousel** (`/itc`) → ring SVG diagram at the **top of the page** shows A→B→C→A with ₹512 cr; three company cards below it, all CRITICAL band. An amber badge identifies the DGGI-derived fixture and redacted names.
5. **Evergreening** (`/evergreening`) → shimmer skeleton animates during load (not plain text). After load, a grey badge reads "SFIO / RBI public-record pattern - graph fixture active" and the 4-column metrics grid appears.
6. **Reports** → open the Reports view and click any available quick-target chip to download a PDF dossier.
7. **Health** → http://localhost:8000/health/ml should show `loaded: true, feature_width: 45`, and `/analyse/U45201MH2005PTC155294` should return non-null `p_fraud_calibrated` and `p_fraud_interval`.

---

## 6 · Troubleshooting

### Port already in use

```bash
# Find what's bound (Windows / Git Bash)
netstat -ano | findstr ":8000" | findstr LISTENING
# Kill the PID
powershell -NoProfile -Command "Stop-Process -Id <PID> -Force"
```

Or change the port: `uvicorn ... --port 8001`, `npm run dev -- --port 5174`.

### `/analyse` returns `500 RuntimeError: Neo4j driver not initialised`

The backend booted but couldn't authenticate to Neo4j. Check the startup log for `lifespan: Neo4j connect failed`. Fix:

```bash
# Confirm the container password matches your .env.local
docker exec sentinel-g-neo4j sh -c 'env | grep NEO4J_AUTH'
# Expect: NEO4J_AUTH=neo4j/sentinel_dev_pwd
# Set NEO4J_PASSWORD in .env.local to match, restart backend (Ctrl+C + re-run).
```

### `/analyse` returns `429 Rate limit exceeded`

Your `RATE_LIMIT_PER_MIN` is too low for development. Edit `.env.local` and bump it (e.g. to `2000`), then restart the backend.

### Meta-learner returns null `p_fraud_calibrated` / `p_fraud_interval`

`/health/ml` shows `loaded: false` and the log has `ml_inference: failed to load artefacts`. Check that all three artifact files exist:

You can also check the artefact files exist:

```bash
ls ml/artifacts/f1a_oof.joblib ml/artifacts/f1b_isotonic.joblib ml/artifacts/f1c_conformal.joblib
```

### `uv sync` fails on `pdftopng`

Append `--no-install-package pdftopng`. That sdist has no Windows wheel and is only used by an optional OCR pipeline; the app boots cleanly without it.

### Neo4j container won't start

Check the volumes — Neo4j 5 enforces strict file ownership and may complain if `infra/.neo4j-data` was created by another image. Nuke and restart:

```bash
docker compose -f infra/docker-compose.dev.yml down -v
rm -rf infra/.neo4j-data infra/.neo4j-logs infra/.neo4j-plugins
docker compose -f infra/docker-compose.dev.yml up -d
```

(Loses local Neo4j state — re-seed via section 2.6.)

### Frontend can't reach backend (`CORS` error / `Network Error`)

Confirm both are on the same host. If you're hitting the Vercel preview URL but want to test against your local backend, the production frontend won't reach `localhost:8000` — use http://localhost:5173 directly, or tunnel your backend via cloudflared and add the tunnel URL to `CORS_ALLOWED_ORIGINS` in `.env.local`.

---

## 7 · Running the test suite

```bash
# Full backend + ML suite (~50 s)
uv run pytest backend/tests ml/tests -q

# Frontend typecheck + production build (~10 s)
cd frontend && npx tsc --noEmit && npm run build
```

Both should be green from any clean checkout. CI (`.github/workflows/ci.yml`) runs the same commands on every PR.

---

## 8 · What does each service do?

| Process | Listens on | Reads from | Writes to | Purpose |
|---|---|---|---|---|
| Neo4j (Docker) | `7474` (HTTP) `7687` (Bolt) | `infra/.neo4j-data` volume | same | Local graph store for companies, signals, and evidence |
| Backend (uvicorn) | `8000` | `.env.local`, Neo4j, `infra/seeds/*.json`, `ml/artifacts/*.joblib` | Neo4j and local runtime stores | FastAPI routes: `/analyse`, `/upload`, `/report`, `/narrative`, `/gonka`, `/health/*` |
| Frontend (Vite) | `5173+` | backend at `VITE_API_BASE` (default `http://localhost:8000`) | nothing on disk | React UI: Dashboard, Graph Explorer, ITC Carousel, Reports, Upload |

Production path: Browser → Vercel frontend → Railway FastAPI backend → GonkaRouter.
Railway does not provide the local Neo4j/GDS development environment.
