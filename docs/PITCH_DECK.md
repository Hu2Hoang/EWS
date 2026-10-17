# Pitch Deck Content

The organisers' template is mandatory. Copy each slide's title and body into the matching template slide, and keep
their order of sections if it differs. The titles are written as headlines (each states its point), and the speaker
notes are for the live pitch. Screenshots to take from the app are marked 📸.

---

## 1. Title

**Early Warning: see bad loans coming, and see who they will take down**
Graph-aware early warning for non-performing loans · BFSI track · Google Cloud AI Builder Cup 2026
Team / names · Live demo URL · GitHub · Video

---

## 2. Problem: banks find bad loans after it is too late to act

- The standard trigger, **30+ days past due**, fires when the borrower is already failing. For Customer A it fires in July, two months after our warning and two months before default.
- Risk is **contagious**: a borrower looks healthy while its key partner, guarantor or industry is failing. Row-by-row scoring cannot see this.
- AI scores that cannot explain themselves are **unusable in credit**: regulators and credit committees need evidence and an audit trail.

> Speaker note: one sentence per bullet. The pain is *late* + *blind to contagion* + *black box*.

---

## 3. Insight: default risk travels along relationships

- Borrowers are connected through related companies, shared guarantors, transactions and industries.
- When one node fails, the risk reaches its neighbours **before** it shows up in their own figures.
- So the right data structure is a **knowledge graph**, and each decision needs only a small **context graph**: what matters for this borrower at this month.

📸 Case review graph: Customer A with its context lit up over the dimmed full graph.

---

## 4. Solution: one loop from signal to measured action

**Detect → Explain → Simulate contagion → React to news → Act and measure**

| Step | What the officer gets |
|---|---|
| Detect | 6 policy-cited early-warning rules plus a contagion score from the graph, re-run for any month (time travel) |
| Explain | A Gemini analyst that cites graph nodes and policies for every claim |
| Simulate | Click any counterparty to make it default and watch the cascade, wave by wave and round by round |
| React | Paste news (English or Vietnamese). Gemini extracts the event and links it to the graph, and the impact is previewed before anything changes |
| Act | Approved proposals become tasks with SLAs. Completing one applies the mitigation, and the rules measure the effect |

---

## 5. Contagion engine: risk flows along the graph, defaults cascade in rounds

- Risk reaching borrower *i* = sum over paths of `α^length × relation weight × share × own risk of the source`
  (related company 0.6, guarantor 0.5, transaction 0.4, industry 0.15). No look-ahead: a relation counts only from its start month.
- **Cascade:** the shocked node defaults. A borrower that the shock pushes over the default threshold defaults in the next round and spreads in turn.
- **Contagion matrix:** which counterparties spread risk into which industry today, which tells the bank whom to ring-fence first.
- Deterministic and explainable: the LLM takes no part in it.

📸 Contagion lab: the cascade graph with a red domino default, and the matrix heatmap.

> Speaker note: "Largest healthy counterparty defaults: 15 borrowers hit, 1 domino default, 798 bn VND touched, in half a second."

---

## 6. Gemini plays three roles and sets no score

| Role | How |
|---|---|
| **Case analyst** | Function calling (context graph, policy, shared-company exposure), then schema-constrained JSON. The backend verifies every node id and policy id |
| **Portfolio assistant** | Multi-turn function calling over typed tools (search, summary, stress test). Answers cite customers that open in the UI |
| **News reader** | Structured extraction (entity, event type, severity, quote) from English or Vietnamese news |

**Guardrails:** scores come only from rules. Names are masked into tokens before any call (the tokens double as exact
entity links). There is a rule-based fallback for every feature, and every decision goes into an audit log.

---

## 7. Human in the loop: every action is measured

- Gemini proposes, the officer approves, and a task appears on the Action board with a deadline.
- When it is done, the mitigation enters the graph (collateral topped up, failing partner ring-fenced, loan rescheduled) and the **same rules re-score**.
- Customer A: collateral **80 → 65**, ring-fence **65 → 50**, contagion inflow **halved**. It stays High because its sales are still falling, and we show that honestly.
- Every news event and mitigation can be undone. Source data is never modified.

📸 Action board with a Done card showing *High → High, 80 → 65*.

---

## 8. Results: earlier warning, far fewer false alarms

Backtest on 1,200 **synthetic** loans (73 defaulters):

| | Early Warning | Legacy rule (DPD ≥ 30) |
|---|---|---|
| Defaulters caught | 73 / 73 | 73 / 73 |
| Average warning before default | **120 days** | 61 days |
| False positives | **14** | 96 |
| Precision | **0.84** | 0.43 |

> Speaker note: say it once: "Synthetic data shows the mechanism, not real-world performance." Judges trust that.

---

## 9. Architecture on Google Cloud

```
Neo4j knowledge graph ──► Context builder ──► Rules engine ──► score + level
                                   │                 │
                                   ▼                 ▼
                          Contagion engine      Gemini (3 roles, tools, structured output)
                                   │                 │
            News / mitigations ──► live overlays (reversible)
                                   ▼
             Angular UI + FastAPI in one container on Cloud Run (built by Cloud Build)
```

- **Cloud Run:** UI and API in one service and one URL, with a warm instance.
- **Gemini API:** function calling, structured output, multilingual extraction.
- **Cloud Build + Artifact Registry:** `gcloud run deploy --source .`
- Resilience: a graph snapshot and pre-warmed Gemini answers ship in the image.

---

## 10. Impact and next steps

- **For a bank:** earlier action while restructuring is still possible, fewer false alarms for officers, and contagion visible at portfolio level.
- **Next:** onboard anonymised bank data (an import pipeline already exists in `backend/scripts/import_real.py`).
  Calibrate contagion weights on real defaults. Stream news through Pub/Sub. Move secrets to Secret Manager and
  persist the live state in a managed database.

---

## 11. Demo and links

Live: `<cloud-run-url>` · Code: github.com/Hu2Hoang/EWS · Video: `<link>`

📸 One collage: Command center, Contagion lab, News to graph, Action board.
