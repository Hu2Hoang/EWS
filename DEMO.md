# 3-Minute Demo Script

Before recording: `backend/.venv/bin/python -m scripts.warm_cache` (so Gemini answers load instantly), backend and frontend running, browser at 1440×900, zoom 100%.

Story: **a credit officer at a mid-size bank who wants to see trouble months before the 30-day-past-due rule fires.**

| Time | Screen | Say | Do |
|---|---|---|---|
| 0:00 | Command Center (opens on May 2026) | "Banks flag a loan as bad only after it is 30 days past due. By then it is too late. We watch 1,200 loans and flag them while there is still time to act." | Point at the tiles: High 80, **+24 new High this month** |
| 0:20 | Command Center | "Every month the orange line is the old rule, the bars are our early warnings. We are months ahead." | Click **Play Timeline**, let it run to Sep |
| 0:45 | Heatmap | "Risk is not spread evenly. Seafood is in a downturn." | Click the **Seafood** row |
| 0:55 | Case Review, Customer A | "Customer A. Gray nodes are the full Knowledge Graph. The bright part is the Context Graph: only what matters for this decision." | Drag the Time Travel slider back to **Apr 2026**, then forward to **May** |
| 1:15 | Signals | "The score comes from transparent rules, never from the LLM. Each signal cites its policy." | Click a signal, the graph focuses on the evidence node |
| 1:30 | Gemini analysis | "Gemini calls tools: it reads the graph, looks up policy, checks contagion. Every claim cites graph ids, and the backend rejects any id that does not exist. Customer names are masked before they reach the model." | Show the tool-call chips, the evidence links, the **PII masked** note |
| 1:55 | Actions | "The AI only proposes. The officer decides, and it is audited." | Type a note, click **Approve**, show the audit log entry |
| 2:10 | Stress Test | "What if the property market slumps?" | Click **Property market slump**: High goes 73 → 194, High-risk exposure more than doubles (3.0 → 6.3 trillion VND). Click the top loan |
| 2:30 | Ask Gemini | "Or just ask." | Ask: *Who shares a related company with Customer A?* Show 7 customers, click a citation |
| 2:45 | Command Center / backtest chips | "On our synthetic backtest: all defaulters caught, 120 days of warning instead of 61, 84% precision instead of 43%. Synthetic data, so it shows the mechanism, not real-world performance. Deployed on Cloud Run with Gemini and Neo4j." | End on the header KPIs |

Tips
- Keep a second tab open on the same URL as a fallback.
- Say the synthetic-data caveat out loud once. Judges trust it more.
- If Gemini is slow, the pre-warmed answers for Customer A (Mar to Sep) load instantly.
