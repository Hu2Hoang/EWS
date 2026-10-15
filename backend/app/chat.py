"""Portfolio assistant: a credit officer asks questions in natural language, Gemini answers by calling typed tools.

Gemini never queries the database freely and never scores. It only calls the tools below, which read the
in-memory graph and the rules engine, so every figure in an answer comes from a tool result. The ids
returned in `cited` let the UI highlight the same customers on the graph.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from app import privacy
from app.scoring import evaluate, month_floor
from app.whatif import simulate

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MAX_ROUNDS = 4

SYSTEM = """You are the portfolio assistant of an early-warning system for non-performing loans. Answer in English,
briefly (at most 6 short lines or bullets), for a bank credit officer.
Rules:
- Use only data returned by the tools. Never invent customers, figures or policies.
- You do NOT score. Risk levels and signals come from the rules engine through the tools.
- Refer to customers by name, never by internal id. Amounts are VND millions.
- For questions about shared exposure use find_customers with shares_company_with or via the customer summary.
- For "what if" questions use run_stress_test. Report how many customers move up a level and the High-risk exposure change.
- If a tool returns nothing, say so plainly. Suggest the officer verify before acting; actions are proposals."""


def answer(history: dict, question: str, as_of: str, previous: list[dict] | None = None) -> dict:
    as_of = month_floor(as_of)
    custs, trends = history["customers"], history["industry_trends"]
    cited: list[str] = []
    trace: list[dict] = []
    guard = privacy.for_history(history)
    masked_before = guard.masked
    by_name = {c["name"].lower(): cid for cid, c in custs.items()}

    def _id(ref: str) -> str:
        """Accepts a customer id or a name (the model sees masked names and may pass one back)."""
        return ref if ref in custs else by_name.get(ref.lower(), ref)

    def _row(cid: str, c: dict, r: dict) -> dict:
        return {"customer_id": cid, "name": c["name"], "industry": c["industry"], "segment": c["segment"],
                "level": r["level"], "score": r["score"], "signals": [s["code"] for s in r["signals"]],
                "loan_amount": c["loan_amount"]}

    def portfolio_overview() -> dict:
        """Counts of customers per risk level, loan exposure per level and counts per industry, as of the assessment date."""
        counts, exposure, ind = {"Low": 0, "Medium": 0, "High": 0}, {"Low": 0, "Medium": 0, "High": 0}, {}
        for c in custs.values():
            r = evaluate(c, trends, as_of)
            counts[r["level"]] += 1
            exposure[r["level"]] += c["loan_amount"]
            row = ind.setdefault(c["industry"], {"High": 0, "Medium": 0, "Low": 0})
            row[r["level"]] += 1
        return {"as_of": as_of, "customers": len(custs), "by_level": counts,
                "exposure_by_level": {k: round(v) for k, v in exposure.items()}, "by_industry": ind}

    def find_customers(industry: str = "", level: str = "", min_signals: int = 0, signal_code: str = "",
                       segment: str = "", shares_company_with: str = "", limit: int = 8) -> dict:
        """Find customers. Filters: industry (Seafood, Real Estate, Textiles, Logistics, Agriculture, Retail), level (High, Medium, Low),
        min_signals, signal_code (revenue_drop, negative_cashflow, related_overdue, collateral_drop, dpd, industry_downturn),
        segment (SME, Mid-Market, Large Corporate), shares_company_with (a company id such as CP002 or a customer id).
        Returns the matches sorted by signals then score, plus the total match count."""
        company_ids: set[str] = set()
        if shares_company_with:
            shares_company_with = _id(shares_company_with)
            if shares_company_with in custs:
                company_ids = {r["id"] for r in custs[shares_company_with]["related"]}
            else:
                company_ids = {shares_company_with}
        rows = []
        for cid, c in custs.items():
            if industry and c["industry"].lower() != industry.lower():
                continue
            if segment and c["segment"].lower() != segment.lower():
                continue
            if company_ids and not ({r["id"] for r in c["related"]} & company_ids) or cid == shares_company_with:
                continue
            r = evaluate(c, trends, as_of)
            if level and r["level"].lower() != level.lower():
                continue
            if r["signal_count"] < min_signals:
                continue
            if signal_code and signal_code not in {s["code"] for s in r["signals"]}:
                continue
            rows.append(_row(cid, c, r))
        rows.sort(key=lambda x: (-len(x["signals"]), -x["score"]))
        top = rows[:max(1, min(limit, 15))]
        cited.extend(x["customer_id"] for x in top)
        return {"total_matches": len(rows), "customers": top}

    def get_customer_summary(customer_id: str) -> dict:
        """Signals, related companies and guarantors of one customer (use the customer_id from find_customers)."""
        customer_id = _id(customer_id)
        c = custs.get(customer_id)
        if not c:
            return {"error": f"Unknown customer {customer_id}"}
        r = evaluate(c, trends, as_of)
        cited.append(customer_id)
        return {**_row(customer_id, c, r), "signal_details": [{"code": s["code"], "label": s["label"], "policy": s["policy"]}
                                                                for s in r["signals"]],
                "related_companies": [{"id": x["id"], "name": x["name"]} for x in c["related"]],
                "guarantors": [g["name"] for g in c["guarantors"]],
                "default_date": c["default_date"]}

    def run_stress_test(industry: str = "", industry_drop_pct: int = 0, failed_company_id: str = "",
                        collateral_drop_pct: int = 0) -> dict:
        """Simulate a shock without changing data: industry revenue drop (percent), a related company failing (company id),
        and/or a collateral value drop (percent). Returns customers by level before/after, High-risk exposure and the top customers that move up."""
        res = simulate(history, as_of, {industry: float(industry_drop_pct)} if industry and industry_drop_pct else {},
                       [failed_company_id] if failed_company_id else [], float(collateral_drop_pct))
        cited.extend(f["id"] for f in res["flipped"][:12])
        return {k: res[k] for k in ("before", "after", "exposure_high_before", "exposure_high_after", "flipped_count")} | {
            "top_flipped": [{"name": f["name"], "industry": f["industry"], "before": f["before"], "after": f["after"],
                             "loan_amount": f["loan_amount"]} for f in res["flipped"][:8]]}

    tools = {f.__name__: f for f in (portfolio_overview, find_customers, get_customer_summary, run_stress_test)}
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    contents = [types.Content(role=m["role"], parts=[types.Part(text=guard.mask_text(m["text"]))]) for m in (previous or [])[-6:]]
    contents.append(types.Content(role="user", parts=[types.Part(text=guard.mask_text(f"(Assessment date: {as_of}) {question}"))]))
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        seen: dict[str, dict] = {}
        text = ""
        for round_no in range(MAX_ROUNDS + 1):
            use_tools = round_no < MAX_ROUNDS  # the last round has no tools, which forces a written answer
            resp = client.models.generate_content(model=model, contents=contents, config=types.GenerateContentConfig(
                system_instruction=SYSTEM, temperature=0.2, tools=list(tools.values()) if use_tools else None,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
            parts = resp.candidates[0].content.parts
            calls = [p.function_call for p in parts if p.function_call]
            if not calls or not use_tools:
                text = "".join(p.text or "" for p in parts if not p.function_call).strip()
                if text:
                    break
                contents.append(types.Content(role="user", parts=[types.Part(text="Answer now using the data above.")]))
                continue
            contents.append(resp.candidates[0].content)
            replies = []
            for fc in calls:
                args = guard.unmask(dict(fc.args or {}))  # the model may pass back a masked token
                key = f"{fc.name}:{json.dumps(args, sort_keys=True)}"
                if key not in seen:
                    try:
                        seen[key] = guard.mask(tools[fc.name](**args)) if fc.name in tools else {"error": "unknown tool"}
                    except Exception as e:  # a bad argument from the model must not end the conversation
                        seen[key] = {"error": f"{type(e).__name__}: {e}"}
                    trace.append({"tool": fc.name, "args": args})
                    out = seen[key]
                else:
                    out = {"note": "Already called with the same arguments; reuse the earlier result."}
                replies.append(types.Part.from_function_response(name=fc.name, response={"result": out}))
            contents.append(types.Content(role="user", parts=replies))
        if not text:
            raise ValueError("Gemini returned no answer")
        text = guard.unmask_text(text)
        source = "gemini"
    except Exception as e:
        text, source = f"The assistant is unavailable right now ({type(e).__name__}). Use the filters and the What-if tab.", "fallback"
    ids = list(dict.fromkeys(cited))[:15]
    return {"answer": text, "source": source, "tool_calls": trace, "cited": [{"id": i, "name": custs[i]["name"]} for i in ids if i in custs], "as_of": as_of,
            "privacy": {"pii_masked": True, "replacements": guard.masked - masked_before}}
