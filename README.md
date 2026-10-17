# Early Warning: AI Early Detection of Non-Performing Loans

> Google Cloud AI Builder Cup 2026 · Theme: **BFSI, Intelligent Risk, Fraud & Financial Experiences**

Traditional early-warning systems fire when a loan is already past due, and they cannot say *why* a loan was flagged.
**Early Warning** combines a **Knowledge Graph**, a decision-scoped **Context Graph**, a transparent **rules engine** and a
**Gemini agent** to flag deteriorating loans earlier, explain every flag with cited evidence, and keep a human credit
officer in charge of the final decision.

## How It Works

```
Synthetic bank data (1,200 loans) ──► Knowledge Graph (Neo4j AuraDB)
                              │  one customer + one "as of" month
                              ▼
                      Context Builder  ──►  Context Graph (only what matters to this decision)
                              │
                              ▼
                  Rules engine (6 signals, policy-backed)  ──►  score + risk level
                              │
                              ▼
        Gemini agent (function calling: context, policy lookup, contagion check)
                              │  schema-constrained JSON, verified against the graph
                              ▼
        Angular UI: graph · time travel · explanation · approve / reject · audit log
```

**What you can do in the app**

| View | What it does |
|---|---|
| **Command Center** | Portfolio KPIs, risk mix over time with a **Play Timeline** button, industry heatmap, and the loans that newly turned High risk this month |
| **Case Review** | One loan: Knowledge Graph with its Context Graph lit up, time-travel slider, rule signals, Gemini analysis with cited evidence, approve / reject, audit log |
| **Contagion Lab** | Make any counterparty (or borrower) default and watch the risk spread wave by wave through shared companies, guarantors and transaction partners. Borrowers pushed over a default threshold fall in the next round (domino defaults). Sliders for contagion strength, path length and threshold; click any node to add it to the shock. Below it, the **contagion matrix**: which sources spread risk into which industry today, click a cell to see who receives it |
| **News to Graph** | Paste a headline or article (English or Vietnamese). Gemini extracts the credit-risk events, the entities are linked to graph nodes through the PII tokens, and the impact is previewed before anything changes. The officer applies or dismisses each event; applied events re-score the portfolio and can be undone. *Play news feed* replays sample headlines as a live stream |
| **Action Board** | Approved actions become tasks with an SLA (Kanban, drag and drop). Completing a task applies the mitigation (collateral top-up, ring-fencing a past-due related company, loan rescheduling) and the rules measure what it bought: score, level, signals cleared, contagion inflow |
| **Stress Test** | What-if lab: industry downturn, related-company failure, collateral shock. The same rules re-score the portfolio and show which loans move up a level |
| **Ask Gemini** | Chat with the portfolio. Gemini answers by calling typed tools (find customers, summarise a customer, run a stress test) and links every answer to cases |

**Design principles**

- **The LLM never scores.** Signals, score and risk level come from deterministic rules. Gemini only explains and proposes.
- **Every claim is traceable.** Each finding cites graph node ids and a policy id. The backend checks both exist and strips
  anything that does not.
- **Working memory, not a data dump.** The Context Graph is the small subgraph relevant to one decision at one point in time.
- **Human in the loop.** Actions are proposals. Approvals and rejections are written to an audit log.
- **Closed loop, reversible.** Confirmed news events and completed mitigations are live overlays on top of the loaded data
  (`backend/app/overlays.py`, stored in `backend/data/live/`). Neo4j is never modified, every overlay can be undone, and
  `POST /live/reset` returns to the loaded data.
- **Graceful degradation.** If Gemini is unavailable the API returns a rule-generated explanation instead of failing.
  If Neo4j is unreachable the API serves the last saved snapshot of the graph.
- **PII stays home.** Names of customers, related companies and guarantors are replaced by tokens before any text goes to
  Gemini and restored in the answer (`backend/app/privacy.py`). The UI shows how many values were masked.

### Early-Warning Signals

| Signal | Fires when | Policy |
|---|---|---|
| Revenue drop | 3-month average revenue is down 15% or more vs. the baseline period | P-17 |
| Negative cash flow | Net cash flow is negative for 2 consecutive periods | P-17 |
| Related company past due | Any related company has days past due above 0 | P-31 |
| Collateral decline | Collateral is 9% or more below the peak of the last 6 valuations | P-22 |
| Days past due | The loan is 10 or more days past due (extra weight at 30+) | P-08 |
| Industry downturn | Industry growth is at or below -3% for 2 consecutive months | P-17 |

Risk level follows policy P-17: **3+ signals = High**, 2 = Medium, otherwise Low.

## Demo Scenario

**Customer A** (Seafood, SME) deteriorates quietly over 2026:

| Month | Event |
|---|---|
| Jan 2026 | Seafood industry enters a downturn |
| Feb to Apr | Revenue falls, about 35% cumulative. Apr: **Medium** (2 signals), related company becomes past due |
| May 2026 | Collateral revalued, down 10%. **Early Warning fires: High, 5 signals** |
| Jun 2026 | Days past due reaches 12 |
| Jul 2026 | Legacy rule (DPD ≥ 30) finally flags the loan |
| Sep 2026 | Default |

Customer A shares its related company with 7 other borrowers (all Low risk today), which is the contagion story for the stress test and the assistant.

Use the **Time Travel** slider to watch the context, the score and the explanation change month by month.

### Backtest

Run on 1,200 **synthetic** loans (73 defaulters, 96 designed as legacy-rule false alarms, 72 that were temporarily stressed and recovered, 959 healthy):

| | Early Warning | Legacy rule (DPD ≥ 30) |
|---|---|---|
| Defaulters caught | 73 / 73 | 73 / 73 |
| False positives | 14 | 96 |
| Precision | 0.84 | 0.43 |
| Average warning before default | 120.2 days | 60.8 days |

> **Read this honestly.** The data and the thresholds were designed together, so these numbers show the mechanism working.
> They are **not** evidence of real-world performance. A real deployment needs backtesting on historical bank data.

## Tech Stack

| Layer | Technology |
|---|---|
| Knowledge Graph | Neo4j AuraDB Free (Cypher) |
| Backend | Python 3.12, FastAPI, `neo4j` driver |
| LLM | Gemini via `google-genai` (default `gemini-3.1-flash-lite`, configurable), function calling plus structured output |
| Frontend | Angular (standalone components, signals), Cytoscape.js |
| Package tooling | `uv` (Python), `npm` with Node 22 (frontend) |

## Project Structure

```
backend/
  app/
    db.py            Neo4j connection
    repository.py    Loads the graph into memory once; all as-of filtering happens in Python
    scoring.py       Signals, score, risk level (rules only)
    context.py       Context Builder: the decision-scoped subgraph for a customer and month
    agent.py         Gemini agent: tool loop, schema output, verification, fallback, cache
    whatif.py        Stress test: shock a copy of the portfolio and re-run the rules
    portfolio.py     Command Center aggregates: risk mix, heatmap, monthly series
    chat.py          Portfolio assistant: Gemini function calling over typed tools
    privacy.py       Pseudonymisation of names before they reach the LLM
    backtest.py      Early Warning vs. legacy rule on labelled synthetic data
    decisions.py     Approve / reject records and the audit log (stored in Neo4j)
    main.py          FastAPI routes
  scripts/
    seed.py          Generates synthetic data and loads it into Neo4j
    warm_cache.py    Pre-runs Gemini for the demo checkpoints
frontend/            Angular app (Command Center, Case Review, Stress Test, assistant)
Dockerfile           One container for Cloud Run: Angular build + FastAPI under /api
deploy.sh            gcloud run deploy from source
```

### Graph Model

```
(Customer)-[:BORROWS]->(Loan)-[:SECURED_BY]->(Collateral)-[:HAS_VALUATION]->(Valuation)
(Customer)-[:OPERATES_IN]->(Industry)-[:HAS_TREND]->(IndustryTrend)
(Customer)-[:IN_SEGMENT]->(Segment)
(Customer)-[:RELATED_TO]->(Company)
(Guarantor)-[:GUARANTEES]->(Loan)
(Customer|Company)-[:HAS_SNAPSHOT]->(Snapshot {month, revenue, net_cashflow, dpd})
(RiskEvent)-[:AFFECTS]->(Customer|Collateral|Industry)
(Decision)-[:ON]->(Customer)        # audit log
(Policy {id, title, text})
```

## Getting Started

### Prerequisites

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/)
- Node.js **22 or newer** (Angular requires it; `nvm install 22` works)
- A free [Neo4j AuraDB](https://neo4j.com/cloud/aura-free/) instance
- A [Gemini API key](https://aistudio.google.com/apikey)

### 1. Configure the Backend

```bash
cd backend
cp .env.example .env        # then fill in the values below
uv venv
uv pip install -r requirements.txt
```

| Variable | Description |
|---|---|
| `NEO4J_URI` | Aura connection URI, e.g. `neo4j+s://<id>.databases.neo4j.io` |
| `NEO4J_USERNAME` | Usually `neo4j` |
| `NEO4J_PASSWORD` | Password shown when the Aura instance was created |
| `NEO4J_DATABASE` | Usually `neo4j` |
| `GEMINI_API_KEY` | Your Gemini API key |
| `GEMINI_MODEL` | Model id, default `gemini-3.1-flash-lite` |

`.env` is git-ignored. Never commit credentials.

### 2. Load the Synthetic Data

```bash
.venv/bin/python -m scripts.seed
```

> **Warning:** this deletes everything in the target database, including recorded decisions. Use a dedicated instance.
> The data is generated with a fixed random seed, so every run produces the same graph.

### 3. Run the API

```bash
.venv/bin/uvicorn app.main:app --port 8000
.venv/bin/python -m uvicorn app.main:app --port 8000
```

Open <http://localhost:8000/docs> for the interactive API documentation.

### 4. Pre-warm the Gemini Cache (Recommended Before Recording a Demo)

```bash
.venv/bin/python -m scripts.warm_cache
```

This runs the agent for Customer A at the key months and stores the results in `backend/.cache/`. Cached results load
instantly and act as a safety net if the network or API quota fails during the demo. The cache is git-ignored, so each
developer builds their own.

### 5. Run the Frontend

```bash
cd frontend
nvm use                    # reads .nvmrc (Node 22); skip if `node -v` already shows 22+
npm install
npm start                   # http://localhost:4200
```

> If `npm start` says the Angular CLI needs a newer Node.js, your shell is still on an older version. With nvm installed, run `source ~/.nvm/nvm.sh && nvm use 22` first.

The UI talks to `http://localhost:8000` when served from `localhost`. State is kept in the URL, so
`?c=C_A&m=2026-04` opens Customer A at April 2026.

## Deploy to Cloud Run

The `Dockerfile` builds the Angular app and serves it from the same FastAPI service (UI at `/`, API at `/api`), so there is one URL and no CORS.

```bash
gcloud auth login
PROJECT_ID=your-project ./deploy.sh      # reads backend/.env, deploys with `gcloud run deploy --source .`
```

- The image includes `backend/data/snapshot.json` (a saved copy of the graph) and `backend/.cache/` (pre-warmed Gemini answers),
  so the demo still works if Neo4j or the Gemini quota has a bad moment.
- `deploy.sh` passes credentials as environment variables for speed. For anything beyond a demo, use Secret Manager (`--set-secrets`).
- `--min-instances 1` keeps one instance warm so the first visitor does not wait for the graph to load.

## API Overview

| Method | Route | Purpose |
|---|---|---|
| GET | `/customers?as_of=YYYY-MM-DD` | Portfolio with score, level and signal count at a month |
| GET | `/customers/{id}/context?as_of=` | Context Graph, signals, events and relevant policies |
| GET | `/customers/{id}/timeline` | Score and level for every month (feeds the time-travel chart) |
| GET | `/customers/{id}/explain?as_of=` | Gemini explanation. Add `refresh=true` to re-run, `cached_only=true` to read the cache only |
| POST | `/customers/{id}/decision` | Record an officer's approve / reject decision |
| GET | `/decisions?customer_id=` | Audit log |
| GET | `/portfolio/summary?as_of=` | Command Center: risk mix, exposure, industry heatmap, new High loans, monthly series |
| POST | `/whatif` | Stress test: `industry_shocks`, `failed_companies`, `collateral_drop`. Nothing is written |
| GET | `/companies` | Related companies ranked by dependent loans (for the contagion picker) |
| POST | `/chat` | Portfolio assistant (Gemini with tools); returns the answer, tool calls and cited customers |
| GET | `/backtest` | Early Warning vs. legacy rule metrics |
| GET | `/graph/overview` | Condensed full graph used as the dimmed backdrop |
| POST | `/admin/reload` | Reload the in-memory graph after reseeding |

## Limitations and Honest Notes

- **Synthetic data only.** No real customer data is used anywhere, and all backtest figures come from data we generated.
- **Rules, not machine learning.** Scoring is a transparent weighted rule set. There is no trained PD model or GNN yet;
  with only 7 labelled defaulters a trained model would not be statistically meaningful.
- **LLM output needs review.** Gemini can still phrase an observation too strongly, for example calling a single negative
  cash-flow period evidence of deterioration when it is not a rule signal. The panel therefore shows a
  "Check Before Deciding" note, and the officer makes the final call.
- **Small-model tool use.** `gemini-3.1-flash-lite` tends to loop on tool calls when given tools and a response schema in a
  single request. The agent works around this with a bounded tool loop followed by a separate schema-constrained call.
- **Privacy.** A real deployment would need data minimisation, access control and retention rules for the audit log.

## Roadmap

- A calibrated probability-of-default model alongside the rules
- Policy retrieval (RAG) over a full policy corpus instead of four policy records
- Real data connectors (core banking, credit bureau) behind the existing repository layer

## Hackathon Deliverables

- [ ] Proposal deck (PDF): problem, community benefit, feasibility and scalability
- [ ] Working prototype deployed on Cloud Run or Firebase
- [ ] Public 3-minute demo video

## License

To be decided by the team.

## Real data and contagion (optional)

The demo runs on synthetic data by default. To run on anonymised data from the bank's TiDB (read-only, nothing is written to TiDB or Neo4j):

```
cd backend && pip install pymysql
export TIDB_HOST=... TIDB_USER=... TIDB_PASSWORD=...        # never commit these
python -m scripts.import_real --inventory                   # what is loaded so far, per source
python -m scripts.import_real                               # writes data/snapshot_real.json (git-ignored)
DATA_SOURCE=real uvicorn app.main:app
python -m scripts.calibrate_contagion --snapshot data/snapshot_real.json   # does neighbour risk predict later default?
```

**Loading more data later needs no code change.** Load files into `stg.daily_casa_txn_above_100_mil`, `stg.monthly_KHDN_SPDV`
or `stg.monthly_casa` and re-run the import: each source adds monthly fields (`txn_in`/`txn_out`/`txn_days`, `n_products`/`product_income`,
`casa_eod`/`n_accounts`) only for the months it covers, and transaction edges are rebuilt from every day loaded.
By default an edge exists from the month its first transaction was seen (`--edge-since first_seen`), so the graph never looks ahead;
until transactions from before the label period are loaded, no edges are visible at calibration dates and contagion cannot be validated.
`--edge-since window_start` assumes edges always existed and is for demos only.

`GET /contagion` and `GET /customers/{id}/contagion` return the risk flowing in from related companies, guarantors, industry and
transaction partners (`app/contagion.py`). Relation types it does not know yet get a default weight, so a new relationship table only needs
to emit edges in the snapshot's `edges` list.
