# Demo Video Script (3:40)

**Target length 3:30 to 3:50. The hard limit is 4:00.** Record on the **deployed Cloud Run URL** and show the address bar
in the first shot, because the rules say the video must show working functionality.

Story: **a credit officer wants to see trouble months before the 30-days-past-due rule fires, and to see how it
spreads to other borrowers.** Detect → explain → see the contagion → react to news → act and measure.

## Before Recording

1. `backend/.venv/bin/python -m scripts.warm_cache` (Customer A's Gemini answers load instantly), then redeploy, or
   record locally after running it.
2. Open the app and click **News to graph → Reset demo**, so no event or task is left over from testing.
3. Browser at 1440×900, zoom 100%, a single tab, notifications off. Hide the bookmarks bar.
4. Rehearse once. Gemini calls take about 2 seconds, so cut the waits in editing if needed.

## Script

| Time | Screen | Say | Do |
|---|---|---|---|
| **0:00** | Command center | "Banks call a loan bad once it is 30 days past due. By then it is too late, and nobody sees how one failure drags down the borrowers around it. Early Warning watches a graph of 1,200 loans and flags trouble while there is still time to act." | Show the URL bar. Click **Play timeline**: the bars (early warnings) climb months ahead of the orange line (legacy rule). |
| **0:25** | Case review, Customer A, **Apr → May 2026** | "Customer A, a seafood SME. Grey is the whole knowledge graph; the bright part is the context for this one decision. In May, five signals fire: revenue, cash flow, collateral, the industry, and a related company that is past due." | Drag Time travel from Apr to May. Click the *Related company past due* signal: the graph centres on **An Phat Garment Co.** |
| **0:50** | Gemini analysis | "Gemini explains it, but it never scores. It calls tools to read the graph and the policies, and every claim cites a node and a policy. The backend rejects any id that does not exist, and names are masked before they leave the bank." | Click **Run AI analysis**. Point at the tool-call chips, the evidence links and the *PII masked* line. |
| **1:15** | Graph → **Contagion lab** | "But risk does not stay put. What if An Phat Garment defaults?" | Click the An Phat node, then **Simulate An Phat Garment Co. defaulting**. The cascade animates wave by wave: **8 borrowers hit, 4 newly on contagion watch, 401 bn VND touched.** |
| **1:35** | Contagion lab | "Any node can fail, and the dominoes fall in rounds: a borrower pushed over the default threshold spreads it further." | Click the preset **Largest healthy counterparty defaults**: 15 borrowers hit, **1 domino default** (red). Move the α slider once to show it re-runs live. |
| **1:55** | Contagion matrix (scroll down) | "The matrix shows who spreads risk into which industry today, the counterparties to ring-fence first." | Click the darkest cell. The list of receiving borrowers appears on the right. |
| **2:10** | **News to graph** | "Risk also arrives as news, here in Vietnamese. Gemini extracts the event, its severity and the company, and links it to the graph. Nothing changes until the officer agrees." | Click the sample **Tax enforcement (Vietnamese)**. Show *linked to Truong An Fisheries JSC, exact match* and the preview: **2 borrowers up a level, 14 scores change, 9 newly on watch.** Click **Apply to graph**: the toast confirms. |
| **2:40** | Case review, Customer A, May → **Action plan** | "Now we act. The officer approves a proposal or adds one, and it becomes a task with a deadline." | Back on Customer A (May 2026). Approve *Request additional collateral* (or pick it in **Add task**). Add **Ring-fence related exposure** too. |
| **2:55** | **Action board** | "When a task is done, the mitigation goes into the graph and the same rules measure what it bought: collateral restored, 80 down to 65. Ring-fencing the failing partner: 65 down to 50, and contagion inflow halves. It is still High, because sales and cash flow are still weak. We show that honestly." | Drag the collateral card to **Done**, then complete the ring-fence card. Show the result lines and *Risk points removed*. Optional: back in Case review, the dotted green **RING_FENCED** edge. |
| **3:20** | Ask Gemini (bottom right) | "And anyone can just ask." | Ask *Who shares a related company with Customer A?* Click one cited customer. |
| **3:30** | Header KPIs | "On our synthetic backtest: every defaulter caught, about 120 days of warning instead of 61, 84% precision instead of 43%. Synthetic data shows the mechanism, not real-world results. Running on Cloud Run with Gemini, built with Cloud Build." | End on the KPIs. |

**Voice-over word count:** about 420 words, about 3:30 at a calm pace. If a take runs long, cut the α slider (1:35)
and the matrix click (1:55) first.

## Fallbacks While Recording

- If Gemini is slow or out of quota, the analysis shows *Fallback: rules only* and the news tab says *keyword extractor*.
  Both still work. Re-record that scene later instead of waiting on camera.
- If a scene goes wrong, **Reset demo** and start the scene again: every event and task is reversible.
- The numbers above come from the clean synthetic dataset. They change if events were applied before recording.

## Key Numbers (Clean Dataset)

| Scene | Number |
|---|---|
| Command center, May 2026 | 80 High risk, +24 newly High this month |
| Customer A, May 2026 | High, 5 signals, score 80 |
| An Phat Garment defaults (May 2026) | 8 borrowers hit, 4 newly on watch, 401 bn VND touched |
| Largest healthy counterparty defaults (Sep 2026) | 15 borrowers hit, 1 domino default, 798 bn VND touched |
| Two counterparties default together | 29 hit, 5 rule level-ups, 1,582 bn VND touched |
| Tax enforcement news on Truong An Fisheries JSC | 2 up a level, 14 scores change, 9 newly on watch |
| Customer A tasks (May 2026) | Collateral 80 → 65; ring-fence 65 → 50, contagion inflow 13.6 → 6.4 |
| Backtest | 73/73 caught, 14 vs 96 false positives, precision 0.84 vs 0.43, 120 vs 61 days of warning |
