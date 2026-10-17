# Prototype Performance and Benchmarking

## 1. Detection Quality: Early Warning vs. the Legacy Rule

Backtest on 1,200 **synthetic** loans (73 defaulters, 96 designed false alarms for the legacy rule, 72 stressed then
recovered, 959 healthy). Run with `GET /backtest`.

| Metric | Early Warning | Legacy rule (DPD ≥ 30) |
|---|---|---|
| Defaulters caught (recall) | **73 / 73 (100%)** | 73 / 73 (100%) |
| Average warning before default | **120.2 days** | 60.8 days |
| Extra lead time | **+59.4 days** | baseline |
| False positives | **14** | 96 |
| Precision | **0.84** | 0.43 |

The data is synthetic, so these numbers show the mechanism, not real-world performance.

## 2. Response Times

Measured on a developer laptop against the full 1,200-loan graph in memory, with the API called over HTTP. Median of 3
to 5 runs. Gemini calls use `gemini-3.1-flash-lite` through the Gemini API.

| Operation | Median | Max |
|---|---|---|
| Context graph + rule signals, one loan | 2 ms | 2 ms |
| Time travel: 18-month score timeline | 2 ms | 2 ms |
| Command center summary (cached) | 2 ms | 32 ms |
| Portfolio list, 1,200 loans (cached) | 20 ms | 50 ms |
| Re-score all 1,200 loans for a new month | 49 ms | 49 ms |
| Stress test: shock and re-score the portfolio | 55 ms | 66 ms |
| Contagion matrix (all sources × industries) | 134 ms | 134 ms |
| **Default cascade simulation** (1 seed, 2 hops, up to 4 rounds) | **367 ms** | 445 ms |
| Cascade, 2 seeds, 3 hops | 488 ms | 573 ms |
| Complete a task: apply the mitigation and re-score everyone | 86 ms | 86 ms |
| Create a task with its expected effect | 177 ms | 177 ms |
| **Gemini news extraction + entity linking + impact preview** | **1.4 s** | 1.6 s |
| Gemini portfolio assistant (multi-turn tools) | 3.1 s | 3.1 s |
| Gemini case analyst, fresh run (tool loop + structured output) | 5.1 s | 5.2 s |
| Gemini case analyst, cached | 4 ms | 7 ms |

Everything that does not call Gemini responds in under half a second, so every slider, click-to-default and time-travel
step re-runs live.

## 3. Reliability and Trust Checks (by Design)

| Check | How |
|---|---|
| LLM never scores | Scores and levels come only from the rules engine. Gemini output is advisory |
| Hallucinated evidence removed | Every `evidence_id` and `policy_id` in Gemini's answer is checked against the graph and the policy table, and unknown ones are stripped and reported |
| PII masked | Customer, company and guarantor names are replaced by tokens before every Gemini call (15 values masked in Customer A's analysis) |
| Graceful degradation | If Gemini fails: rule-generated explanation and keyword news extractor. If Neo4j fails: the saved graph snapshot |
| Reversible changes | News events and mitigations are overlays. Every one can be undone, and the source data is never modified |

## Reproduce

```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000   # then call the endpoints above, e.g.
curl -s localhost:8000/backtest
curl -s -XPOST localhost:8000/contagion/shock -H 'content-type: application/json' -d '{"seeds":["CP126"]}'
```
