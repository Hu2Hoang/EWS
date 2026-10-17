import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel

from app import chat as chat_agent, contagion, decisions, live, news, portfolio, repository, whatif
from app.backtest import run_backtest, timeline
from app.context import build_context
from app.scoring import evaluate

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Warm the in-memory graph in the background so the first user request is not the slow one.
    threading.Thread(target=_warm, daemon=True).start()
    yield


def _warm():
    """Loads the data and pre-computes the heavy default views so the first visitor does not wait (seconds on a real portfolio)."""
    repository.load_all()
    for view in (lambda: customers(DEFAULT_AS_OF), lambda: portfolio_summary(DEFAULT_AS_OF), backtest):
        try:
            view()
        except Exception as e:  # warming is best effort
            print(f"warm-up skipped: {type(e).__name__}: {e}", flush=True)


app = FastAPI(title="Early Warning API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:4200").split(","), allow_methods=["*"], allow_headers=["*"])

DEFAULT_AS_OF = "2026-09-01"


def _customer(cid: str) -> dict:
    c = repository.load_all()["customers"].get(cid)
    if not c:
        raise HTTPException(404, f"Unknown customer {cid}")
    return c


@app.get("/health")
def health():
    return {"ok": True, "customers": len(repository.load_all()["customers"])}


@app.post("/admin/reload")
def reload():
    return {"customers": len(repository.reload()["customers"])}


@app.get("/customers")
def customers(as_of: str = DEFAULT_AS_OF):
    return repository.memo(("customers", as_of), lambda: _customer_rows(as_of))


def _customer_rows(as_of: str) -> list:
    h = repository.load_all()
    rows = []
    for cid, c in h["customers"].items():
        r = evaluate(c, h["industry_trends"], as_of)
        rows.append({"id": cid, "name": c["name"], "industry": c["industry"], "segment": c["segment"],
                     "score": r["score"], "level": r["level"], "signal_count": r["signal_count"],
                     "legacy_flag": r["legacy_flag"]})
    return sorted(rows, key=lambda r: (-r["signal_count"], -r["score"]))


@app.get("/customers/{cid}/context")
def context(cid: str, as_of: str = DEFAULT_AS_OF):
    _customer(cid)
    return build_context(repository.load_all(), cid, as_of)


@app.get("/customers/{cid}/contagion")
def customer_contagion(cid: str, as_of: str = DEFAULT_AS_OF, alpha: float = contagion.ALPHA, hops: int = contagion.HOPS):
    """Risk reaching this customer from its neighbours (related companies, guarantors, industry, transaction partners)."""
    _customer(cid)
    return {"as_of": as_of, "alpha": alpha, "hops": hops, **contagion.propagate(repository.load_all(), as_of, alpha, hops)[cid]}


@app.get("/contagion")
def contagion_ranking(as_of: str = DEFAULT_AS_OF, alpha: float = contagion.ALPHA, hops: int = contagion.HOPS, top: int = 20):
    """Customers with the most risk flowing in from their neighbourhood, with the strongest sources of that risk."""
    res = contagion.propagate(repository.load_all(), as_of, alpha, hops)
    names = repository.load_all()["customers"]
    rows = sorted(res.items(), key=lambda kv: (-kv[1]["inflow"], kv[0]))[:max(1, min(top, 200))]
    return {"as_of": as_of, "alpha": alpha, "hops": hops, "watch_threshold": contagion.WATCH,
            "watch_count": sum(v["watch"] for v in res.values()),
            "rows": [{"id": cid, "name": names[cid]["name"], **v} for cid, v in rows]}


class ShockIn(BaseModel):
    as_of: str = DEFAULT_AS_OF
    seeds: list[str]
    alpha: float = contagion.ALPHA
    hops: int = contagion.HOPS
    threshold: float = contagion.DEFAULT_AT


@app.post("/contagion/shock")
def contagion_shock(body: ShockIn):
    """Default cascade from the chosen nodes: which borrowers the risk reaches, hop by hop and round by round."""
    try:
        return contagion.shock(repository.load_all(), body.as_of, body.seeds, body.alpha, body.hops, body.threshold)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/contagion/matrix")
def contagion_matrix(as_of: str = DEFAULT_AS_OF, alpha: float = contagion.ALPHA, hops: int = contagion.HOPS):
    return repository.memo(("matrix", as_of, alpha, hops), lambda: contagion.matrix(repository.load_all(), as_of, alpha, hops))


@app.get("/contagion/source/{sid}")
def contagion_source(sid: str, as_of: str = DEFAULT_AS_OF):
    """Who a source in the contagion matrix is passing risk to right now."""
    return contagion.receivers(repository.load_all(), as_of, sid)


@app.get("/contagion/candidates")
def contagion_candidates(as_of: str = DEFAULT_AS_OF, limit: int = 40):
    """Companies and guarantors to shock, with how many borrowers and how much exposure hang on each."""
    h = repository.load_all()
    nodes, inbound = contagion.build_graph(h, as_of)
    rows = []
    for n, v in nodes.items():
        if v["type"] not in ("Company", "Guarantor"):
            continue
        linked = {s for k in ("related", "guarantor") for s, _ in inbound[n].get(k, [])}
        rows.append({"id": n, "label": v["label"], "type": v["type"], "own": round(v["own"], 2), "customers": len(linked),
                     "exposure": round(sum(h["customers"][c]["loan_amount"] for c in linked))})
    return sorted(rows, key=lambda r: (-r["exposure"]))[:max(1, min(limit, 200))]


@app.get("/customers/{cid}/timeline")
def customer_timeline(cid: str):
    return timeline(_customer(cid), repository.load_all()["industry_trends"])


@app.get("/backtest")
def backtest():
    return repository.memo(("backtest",), lambda: run_backtest(repository.base()))  # on the loaded data, not the demo overlays


@app.get("/customers/{cid}/explain")
def explain(cid: str, as_of: str = DEFAULT_AS_OF, refresh: bool = False, cached_only: bool = False):
    _customer(cid)
    from app.agent import cached, explain as run_agent
    if cached_only:
        return cached(repository.load_all(), cid, as_of)
    return run_agent(repository.load_all(), cid, as_of, refresh)


class DecisionIn(BaseModel):
    as_of: str = DEFAULT_AS_OF
    action: str
    decision: Literal["approved", "rejected"]
    note: str = ""
    officer: str = "Credit Officer"
    ai_source: str | None = None


@app.post("/customers/{cid}/decision")
def decide(cid: str, body: DecisionIn):
    c = _customer(cid)
    if body.action not in decisions.ACTIONS:
        raise HTTPException(422, f"Invalid action: {body.action}")
    r = evaluate(c, repository.load_all()["industry_trends"], body.as_of)
    row = decisions.record(cid, r["as_of"], body.action, body.decision, body.note, body.officer,
                           body.ai_source, r["level"], r["score"])
    if body.decision == "approved" and body.action != "no_action":  # an approved proposal becomes a task on the action board
        row["task"] = live.create_task(cid, body.action, r["as_of"], body.note, body.officer,
                                       "gemini" if body.ai_source == "gemini" else "rules", row["id"])
    return row


@app.get("/decisions")
def audit_log(customer_id: str | None = None):
    return decisions.list_decisions(customer_id)


@app.get("/graph/overview")
def overview(limit: int = 160):
    """Backdrop of the full Knowledge Graph for the UI: the `limit` highest-risk customers at the default date plus a
    sample of healthy ones, with their companies, guarantors and industries. Capped so the browser layout stays fast."""
    h = repository.load_all()
    ranked = sorted(h["customers"].items(),
                    key=lambda kv: (-(r := evaluate(kv[1], h["industry_trends"], DEFAULT_AS_OF))["signal_count"], -r["score"], kv[0]))
    chosen = [cid for cid, _ in ranked[:limit]] + [cid for cid, _ in ranked[limit::max(1, len(ranked) // 40)]][:40]
    nodes, edges, seen = [], [], set()

    def node(i, t, label):
        if i not in seen:
            seen.add(i)
            nodes.append({"id": i, "type": t, "label": label})

    for cid in chosen:
        c = h["customers"][cid]
        node(cid, "Customer", c["name"])
        node(c["industry"], "Industry", f"{c['industry']} industry")
        edges.append({"source": cid, "target": c["industry"], "type": "OPERATES_IN"})
        for r in c["related"]:
            node(r["id"], "Company", r["name"])
            edges.append({"source": cid, "target": r["id"], "type": "RELATED_TO"})
        for g in c["guarantors"]:
            node(g["id"], "Guarantor", g["name"])
            edges.append({"source": g["id"], "target": cid, "type": "GUARANTEES"})
    return {"nodes": nodes, "edges": edges}


@app.get("/portfolio/summary")
def portfolio_summary(as_of: str = DEFAULT_AS_OF):
    return repository.memo(("portfolio", as_of), lambda: portfolio.summary(repository.load_all(), as_of))


class WhatIfIn(BaseModel):
    as_of: str = DEFAULT_AS_OF
    industry_shocks: dict[str, float] = {}
    failed_companies: list[str] = []
    collateral_drop: float = 0.0
    collateral_kind: str | None = None


@app.post("/whatif")
def what_if(body: WhatIfIn):
    return whatif.simulate(repository.load_all(), body.as_of, body.industry_shocks, body.failed_companies,
                           body.collateral_drop, body.collateral_kind)


@app.get("/companies")
def companies(as_of: str = DEFAULT_AS_OF, limit: int = 12):
    """Related companies ranked by how many customers depend on them (for the contagion what-if picker)."""
    h = repository.load_all()
    count: dict[str, dict] = {}
    for c in h["customers"].values():
        for r in c["related"]:
            row = count.setdefault(r["id"], {"id": r["id"], "name": r["name"], "customers": 0})
            row["customers"] += 1
    return sorted(count.values(), key=lambda r: -r["customers"])[:limit]


class ChatIn(BaseModel):
    question: str
    as_of: str = DEFAULT_AS_OF
    history: list[dict] = []


@app.post("/chat")
def chat(body: ChatIn):
    return chat_agent.answer(repository.load_all(), body.question, body.as_of, body.history)


class TaskIn(BaseModel):
    customer_id: str
    action: str
    as_of: str = DEFAULT_AS_OF
    note: str = ""
    officer: str = "Credit Officer"


class StatusIn(BaseModel):
    status: Literal["todo", "doing", "done"]


@app.get("/tasks")
def tasks(customer_id: str | None = None):
    return live.list_tasks(customer_id)


@app.post("/tasks")
def create_task(body: TaskIn):
    _customer(body.customer_id)
    try:
        return live.create_task(body.customer_id, body.action, body.as_of, body.note, body.officer)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/tasks/{task_id}/status")
def task_status(task_id: str, body: StatusIn):
    """Moving a task to done applies its mitigation to the live portfolio and returns the measured effect; moving it back undoes it."""
    try:
        return live.set_status(task_id, body.status)
    except KeyError:
        raise HTTPException(404, f"Unknown task {task_id}")


class NewsIn(BaseModel):
    text: str
    as_of: str = DEFAULT_AS_OF


@app.post("/news/analyze")
def news_analyze(body: NewsIn):
    """Gemini extracts risk events from the text and links them to graph nodes. A dry run: nothing changes until /events."""
    if not body.text.strip():
        raise HTTPException(422, "Paste a headline or an article")
    return news.analyze(repository.load_all(), body.text[:4000], body.as_of)


@app.get("/news/samples")
def news_samples(as_of: str = DEFAULT_AS_OF):
    return news.samples(repository.load_all(), as_of)


class EventIn(BaseModel):
    as_of: str = DEFAULT_AS_OF
    target_type: Literal["Company", "Customer", "Industry"]
    target_id: str
    event_type: str
    severity: Literal["low", "medium", "high"] = "medium"
    description: str
    source: str = "manual"
    text: str = ""
    id: str | None = None  # the draft id from /news/analyze, kept so the UI can track and undo it


@app.get("/events")
def live_events():
    return live.events()


@app.post("/events")
def apply_event(body: EventIn):
    """An officer confirms an event: it joins the graph, the portfolio is re-scored and the impact is returned."""
    try:
        ov = live.new_event(body.as_of, body.target_type, body.target_id, body.event_type, body.severity,
                            body.description, body.source, body.text)
        if body.id:
            if any(e["id"] == body.id for e in live.events()):
                raise HTTPException(409, "This event is already applied")
            ov["id"] = body.id
        return live.apply(ov)
    except (KeyError, ValueError) as e:
        raise HTTPException(422, f"Cannot apply the event: {e}")


@app.delete("/events/{overlay_id}")
def undo_event(overlay_id: str):
    if not live.remove(overlay_id):
        raise HTTPException(404, f"Unknown event {overlay_id}")
    return {"ok": True}


@app.post("/live/reset")
def live_reset():
    """Demo reset: removes every confirmed event and every task, back to the loaded data."""
    live.reset()
    return {"ok": True}


# In the container the Angular build is served by this same service (one Cloud Run URL, API under /api).
_static = Path(__file__).resolve().parent.parent / "static"
if _static.is_dir():
    from fastapi.staticfiles import StaticFiles
    root = FastAPI(title="Early Warning", lifespan=lifespan)
    root.mount("/api", app)
    root.mount("/", StaticFiles(directory=_static, html=True), name="ui")
