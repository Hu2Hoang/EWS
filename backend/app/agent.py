"""Gemini agent: explains early-warning alerts with cited evidence. Gemini does NOT score.

Flow: Gemini picks tools itself (function calling) to fetch the context graph, policies and contagion
exposure through shared companies, then returns JSON matching a schema. The output is then verified:
every evidence_id must exist in the graph, every policy_id must exist, and the risk level comes from
the rules, never from the LLM.
"""
import hashlib
import json
import os
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app import privacy
from app.context import build_context
from app.scoring import evaluate, month_floor

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
MAX_TOOL_ROUNDS = 3
_memory: dict[tuple[str, str], dict] = {}


class ActionType(str, Enum):
    increase_monitoring = "increase_monitoring"
    request_additional_collateral = "request_additional_collateral"
    suspend_disbursement = "suspend_disbursement"
    review_related_exposure = "review_related_exposure"
    no_action = "no_action"


class Finding(BaseModel):
    statement: str = Field(description="One short finding that cites concrete figures returned by the tools.")
    evidence_ids: list[str] = Field(description="Graph node ids used as evidence (e.g. C_A, CP002).")
    policy_id: str = Field(description="Related policy id, e.g. P-17.")


class Action(BaseModel):
    action: ActionType
    rationale: str = Field(description="Why this action, referencing the policy.")
    policy_id: str


class Explanation(BaseModel):
    summary: str = Field(description="2-3 sentence summary for the credit officer, in English.")
    findings: list[Finding]
    recommended_actions: list[Action]
    limitations: str = Field(description="What is unknown or what the officer should double-check.")


SYSTEM = """You are a credit-risk analysis assistant for bank credit officers. Answer in English.
Mandatory rules:
- Do NOT compute or change the score or risk level. The level and the signals come from the rules engine.
- Use only facts returned by the tools. Never invent figures, node ids or policy clauses.
- Every finding must carry evidence_ids (node ids that exist in the graph) and a real policy_id.
- Before concluding: (1) call get_customer_context, (2) call get_policy for each relevant policy,
  (3) call get_shared_company_exposure for any past-due related company to assess contagion risk.
- Only items in `signals` may be called "early-warning signals", and only they may justify the risk level
  and the signal count. Any other fact (for example a single negative cash-flow period that is not in
  signals) may only be mentioned as an "additional observation": do not count it as a signal and do not
  use it as the main reason for an action.
- Refer to the customer by name (the `name` field, e.g. "Customer A"), never by internal id in prose.
  Ids belong only in evidence_ids.
- Call each tool ONCE per argument set; when you have what you need, stop calling tools.
- Actions are PROPOSALS only; the credit officer makes the final decision.
- If there are no meaningful signals, choose no_action."""


def _compact_context(ctx: dict) -> dict:
    a = ctx["assessment"]
    return {
        "customer": ctx["customer"], "as_of": ctx["as_of"],
        "risk_level_by_rules": a["level"], "score_by_rules": a["score"],
        "signals": [{k: s[k] for k in ("code", "label", "detail", "evidence", "policy")} for s in a["signals"]],
        "graph_nodes": [{"id": n["id"], "type": n["type"], "label": n["label"], "flagged": n["flagged"],
                         "props": n["props"]} for n in ctx["graph"]["nodes"]],
        "graph_edges": ctx["graph"]["edges"],
        "events": ctx["events"],
        "recent_snapshots": ctx["history"]["snapshots"][-6:],
    }


def _fallback(ctx: dict, reason: str) -> dict:
    a = ctx["assessment"]
    return {
        "summary": f"{a['level']} risk (score {a['score']}) with {a['signal_count']} early-warning signals.",
        "findings": [{"statement": s["label"], "evidence_ids": s["evidence"], "policy_id": s["policy"]}
                     for s in a["signals"]],
        "recommended_actions": [{"action": "increase_monitoring", "rationale": "Per P-17 when 3+ signals fire.",
                                 "policy_id": "P-17"}] if a["signal_count"] >= 3 else [],
        "limitations": f"Rule-generated explanation, no LLM used ({reason}).",
    }


def _verify(expl: dict, ctx: dict, history: dict) -> dict:
    node_ids = {n["id"] for n in ctx["graph"]["nodes"]}
    policy_ids = set(history["policies"])
    issues = []
    for f in expl["findings"]:
        bad = [e for e in f["evidence_ids"] if e not in node_ids]
        if bad:
            issues.append(f"unknown evidence_ids: {bad}")
            f["evidence_ids"] = [e for e in f["evidence_ids"] if e in node_ids]
        if f["policy_id"] not in policy_ids:
            issues.append(f"unknown policy_id: {f['policy_id']}")
    for act in expl["recommended_actions"]:
        if act["policy_id"] not in policy_ids:
            issues.append(f"unknown policy_id: {act['policy_id']}")
    expl["verification"] = {"ok": not issues, "issues": issues}
    return expl


def _fingerprint(history: dict, customer_id: str, as_of: str) -> str:
    """Hash of the data the explanation is based on, so a cached answer is never shown after the data changes (reseed)."""
    payload = json.dumps(_compact_context(build_context(history, customer_id, as_of)), sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def cached(history: dict, customer_id: str, as_of: str) -> dict | None:
    as_of = month_floor(as_of)
    hit = _memory.get((customer_id, as_of))
    f = CACHE_DIR / f"{customer_id}_{as_of}.json"
    if hit is None and f.exists():
        hit = json.loads(f.read_text())
    return hit if hit and hit.get("fingerprint") == _fingerprint(history, customer_id, as_of) else None


def explain(history: dict, customer_id: str, as_of: str, refresh: bool = False) -> dict:
    as_of = month_floor(as_of)
    key = (customer_id, as_of)
    cache_file = CACHE_DIR / f"{customer_id}_{as_of}.json"
    if not refresh:
        hit = cached(history, customer_id, as_of)
        if hit:
            _memory[key] = hit
            return hit

    ctx = build_context(history, customer_id, as_of)
    trace: list[dict] = []

    def get_customer_context(customer_id: str) -> dict:
        """Get the customer's context graph as of the assessment date: rule signals, nodes, edges, events and the last 6 periods of figures."""
        return _compact_context(ctx)

    def get_policy(policy_id: str) -> dict:
        """Get the text of a credit policy by id (e.g. P-17, P-22, P-31, P-08)."""
        return history["policies"].get(policy_id, {"error": f"No such policy {policy_id}"})

    def get_shared_company_exposure(company_id: str) -> dict:
        """List the other customers linked to the same company (contagion risk) with their current risk level."""
        others = []
        for cid, c in history["customers"].items():
            if cid != customer_id and any(r["id"] == company_id for r in c["related"]):
                r = evaluate(c, history["industry_trends"], as_of)
                others.append({"customer_id": cid, "name": c["name"], "risk_level": r["level"]})
        return {"company_id": company_id, "other_customers": others}

    guard = privacy.for_history(history)
    masked_before = guard.masked
    tools = {f.__name__: f for f in (get_customer_context, get_policy, get_shared_company_exposure)}
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    task = guard.mask_text(f"Analyse the early warning for {ctx['customer']['name']} (customer_id={customer_id}) as of {as_of}.")

    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        gathered: list[dict] = []
        seen: dict[str, dict] = {}
        contents = [types.Content(role="user", parts=[types.Part(text=task)])]
        # Phase 1: Gemini chooses tools. The loop is manual because small models tend to call tools forever.
        for _ in range(MAX_TOOL_ROUNDS):
            resp = client.models.generate_content(model=model, contents=contents, config=types.GenerateContentConfig(
                system_instruction=SYSTEM, temperature=0.2, tools=list(tools.values()),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
            calls = [p.function_call for p in resp.candidates[0].content.parts if p.function_call]
            if not calls:
                break
            contents.append(resp.candidates[0].content)
            replies = []
            for fc in calls:
                args = dict(fc.args or {})
                call_key = f"{fc.name}:{json.dumps(args, sort_keys=True)}"
                if call_key not in seen:
                    seen[call_key] = guard.mask(tools[fc.name](**args)) if fc.name in tools else {"error": "unknown tool"}
                    gathered.append({"tool": fc.name, "args": args, "result": seen[call_key]})
                    trace.append({"tool": fc.name, "args": args})
                    out = seen[call_key]
                else:
                    out = {"note": "Already called with the same arguments; reuse the earlier result."}
                replies.append(types.Part.from_function_response(name=fc.name, response={"result": out}))
            contents.append(types.Content(role="user", parts=replies))
        if not gathered:  # the model called no tool: supply the minimum context ourselves
            args = {"customer_id": customer_id}
            gathered.append({"tool": "get_customer_context", "args": args, "result": guard.mask(_compact_context(ctx))})
            trace.append({"tool": "get_customer_context", "args": args})

        # Phase 2: no tools, force schema-constrained output.
        final = client.models.generate_content(
            model=model,
            contents=f"{task}\n\nData gathered from the tools (JSON):\n"
                     f"{json.dumps(gathered, ensure_ascii=False)}\n\nReturn your conclusion following the schema.",
            config=types.GenerateContentConfig(system_instruction=SYSTEM, temperature=0.2,
                                               response_mime_type="application/json", response_schema=Explanation))
        if not final.text:
            raise ValueError("Gemini returned no final JSON")
        expl = guard.unmask(json.loads(final.text))  # restore real names for the officer
        source = "gemini"
    except Exception as e:  # network/quota/schema problems must never break the demo
        expl, source = _fallback(ctx, f"{type(e).__name__}: {str(e)[:120]}"), "fallback"

    expl = _verify(expl, ctx, history)
    out = {"customer_id": customer_id, "as_of": as_of, "risk_level": ctx["assessment"]["level"],
           "score": ctx["assessment"]["score"], "source": source,
           "model": model if source == "gemini" else None,
           "tool_calls": trace, "privacy": {"pii_masked": True, "replacements": guard.masked - masked_before},
           "fingerprint": _fingerprint(history, customer_id, as_of), **expl}
    if source == "gemini":  # only cache real results; they double as a safety net when recording the demo
        _memory[key] = out
        CACHE_DIR.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out
