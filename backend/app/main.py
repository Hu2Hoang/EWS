import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel

from app import chat as chat_agent, decisions, portfolio, repository, whatif
from app.backtest import run_backtest, timeline
from app.context import build_context
from app.scoring import evaluate

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Warm the in-memory graph in the background so the first user request is not the slow one.
    threading.Thread(target=repository.load_all, daemon=True).start()
    yield


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


@app.get("/customers/{cid}/timeline")
def customer_timeline(cid: str):
    return timeline(_customer(cid), repository.load_all()["industry_trends"])


@app.get("/backtest")
def backtest():
    return run_backtest(repository.load_all())


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
    return decisions.record(cid, r["as_of"], body.action, body.decision, body.note, body.officer,
                            body.ai_source, r["level"], r["score"])


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
    return portfolio.summary(repository.load_all(), as_of)


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


# In the container the Angular build is served by this same service (one Cloud Run URL, API under /api).
_static = Path(__file__).resolve().parent.parent / "static"
if _static.is_dir():
    from fastapi.staticfiles import StaticFiles
    root = FastAPI(title="Early Warning", lifespan=lifespan)
    root.mount("/api", app)
    root.mount("/", StaticFiles(directory=_static, html=True), name="ui")
