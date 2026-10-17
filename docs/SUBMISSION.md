# Submission Pack: Early Warning

Google Cloud AI Builder Cup 2026 · Track: **BFSI (Intelligent Risk, Fraud & Financial Experiences)**
Topic: graph-aware contagion detection to forecast non-performing loans

| Item | Link |
|---|---|
| Deployed prototype | `https://<cloud-run-url>` (fill in after `./deploy.sh`) |
| GitHub repository | <https://github.com/Hu2Hoang/EWS> |
| Demo video (3 to 4 min) | `<YouTube or Drive link, set to "Anyone with the link">` |
| Pitch deck | Built from the organisers' template, content in [`PITCH_DECK.md`](PITCH_DECK.md) |

---

## Brief Description (paste into the form)

### Form version (943 characters; the form allows at most 1024)

Early Warning flags loans about to turn bad months before the "30 days past due" rule, and shows how risk spreads between borrowers. A Neo4j knowledge graph links customers, loans, collateral, industries, related companies and guarantors. Policy-cited rules score each loan; a contagion engine spreads risk along the graph and simulates domino defaults when an officer clicks any node.

Gemini API (function calling + structured output) plays 3 roles: an analyst agent that explains each alert with graph evidence the backend verifies; a portfolio assistant answering questions via typed tools; and a news reader that extracts risk events from English or Vietnamese news and links them to graph nodes. Gemini never sets a score, and names are masked before any call. Approved actions become tasks whose effect is re-measured by the rules.

Cloud Run hosts the UI and API as one container, built by Cloud Build and stored in Artifact Registry.

### Long version (for the README or the deck, too long for the form)

### Part 1: What we built

Early Warning flags loans that are about to go bad months before the classic "30 days past due" rule, and shows how the risk travels between borrowers. A Neo4j knowledge graph links customers, loans, collateral, industries, related companies and guarantors. Transparent, policy-cited rules score each loan. A deterministic contagion engine spreads risk along the graph and simulates default cascades (domino defaults) that a credit officer can trigger by clicking any node. Gemini plays three roles: an analyst agent that explains each alert with cited graph evidence, a portfolio assistant that answers questions through typed tools, and a news reader that extracts risk events from English or Vietnamese text and links them to graph nodes. The officer confirms each event, the portfolio is re-scored live, and approved actions become tasks whose effect is measured by the same rules. Gemini never sets a score: every claim is verified against the graph, and names are masked before any text reaches the model.

### Part 2: How we use Google services

Gemini API (google-genai SDK, model gemini-3.1-flash-lite) powers three features. First, a case analyst agent uses function calling over three tools (context graph, policy lookup, shared-company exposure), then returns a schema-constrained JSON answer. Our backend verifies every cited node id and policy id and strips anything that does not exist. Second, a portfolio assistant holds a multi-turn chat with function calling over four typed tools (portfolio overview, customer search, customer summary, stress test), and every customer it cites opens in the UI. Third, a news-to-graph feature uses structured output to extract the entity, event type, severity and supporting quote from English or Vietnamese news. Known names are replaced by tokens before the call, so the token Gemini returns links to exactly one graph node.

Cloud Run hosts the whole product as a single container: the Angular UI at the root and the FastAPI API under /api, so there is one public URL and no cross-origin setup. One warm instance keeps the in-memory graph loaded so evaluators never hit a cold start.

Cloud Build and Artifact Registry build and store the image: "gcloud run deploy --source ." runs our multi-stage Dockerfile (Node 22 builds the UI, Python 3.12 runs the API).

Outside Google, Neo4j AuraDB holds the knowledge graph and the decision audit log. If it is unreachable, the API serves a saved snapshot. Gemini answers for the key demo months are pre-warmed and shipped in the image, and every Gemini feature has a rule-based fallback (rule explanation, keyword extractor), so an API quota problem degrades the prototype instead of breaking it.

### What makes it different

- **Graph-aware, not row-by-row.** Risk inflow from neighbours is computed over non-backtracking paths
  (`α^len × relation weight × share`), so a healthy-looking borrower tied to a failing counterparty is visible before its own figures turn.
- **Interactive what-if on the graph.** Default any node, watch the cascade wave by wave, tune α, path length and the default threshold.
  The contagion matrix shows who spreads risk into which industry today.
- **Closed loop.** News → graph → re-score → Gemini explanation → officer decision → task → mitigation → measured effect.
  Every change is reversible, and Neo4j is never modified by the demo.
- **Trustworthy AI.** The LLM never scores. Every claim cites graph ids and a policy, PII is masked, and every decision is audited.

### Honest limits

The portfolio (1,200 loans) and the backtest are **synthetic**: they show the mechanism, not real-world performance.
The effects of news events and mitigations are simple, documented rules (for example, a company event makes it 15, 45
or 90 days past due by severity). They are not a calibrated model.

---

## Pre-Submission Checklist

### Deployed prototype
- [ ] `PROJECT_ID=... ./deploy.sh` succeeded and printed the service URL
- [ ] The URL opens in a **private window** (no Google login needed): `--allow-unauthenticated` is set
- [ ] `<url>/api/health` returns `{"ok": true, "customers": 1200}`
- [ ] Every tab loads: Command center, Case review, Contagion lab, News to graph, Action board, Stress test
- [ ] Gemini works in production: run AI analysis on Customer A, ask the assistant one question, analyse one sample headline
- [ ] Click **Reset demo** (News to graph tab) after your own tests, so evaluators start from a clean state
- [ ] Gemini quota: check the key's limits in Google AI Studio. Billing on the key is safer during evaluation
- [ ] Neo4j AuraDB Free **pauses an idle instance after a few days**. Check it in the Aura console before the evaluation,
      or rely on the bundled snapshot (the API falls back automatically)
- [ ] Do not redeploy or delete the service during evaluation. Keep the billing account active

### GitHub repository
- [ ] Repository is **public** (or evaluators are invited)
- [ ] `backend/.env` is **not** committed: `git ls-files backend/.env` prints nothing
- [ ] No secrets in history: `git log -p | grep -iE "api_key|password" | head`
- [ ] README has the live URL, the video link and run instructions
- [ ] All work is committed and pushed to `main`

### Video
- [ ] Length 3:00 to 4:00 (script: [`../DEMO.md`](../DEMO.md))
- [ ] Shows **working functionality** on the deployed URL (the address bar is visible at least once)
- [ ] Link is set to "Anyone with the link can view" and tested in a private window

### Pitch deck
- [ ] Uses the **organisers' template** (format is mandatory)
- [ ] Contains the live URL, GitHub link and video link
