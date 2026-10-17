"""Loads the full history from Neo4j into memory (the dataset is small) and caches it.

All `as_of` filtering then happens in Python on this structure, so the Context Builder,
scoring and backtest share a single source of data.
"""
import json
import os
import threading
from pathlib import Path

from app import contagion, overlays
from app.db import run

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "snapshot.json"
SNAPSHOT_REAL = SNAPSHOT.with_name("snapshot_real.json")


def _by(rows, key):
    out = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return out


_lock = threading.RLock()  # the startup preload and the first requests must not load the graph twice
_base: dict | None = None  # the data as loaded
_data: dict | None = None  # the base plus the live overlays (confirmed news events, completed mitigations)


def load_all() -> dict:
    """Reads the graph from Neo4j once and saves a snapshot. If Neo4j is unreachable the snapshot is used, so a demo survives an outage."""
    global _data
    with _lock:
        if _data is None:
            _data = overlays.apply_all(base())
        return _data


def base() -> dict:
    global _base
    with _lock:
        if _base is None and os.getenv("DATA_SOURCE") == "real":
            _base = json.loads(SNAPSHOT_REAL.read_text())  # anonymised real data; Neo4j is not touched
        if _base is None:
            try:
                _base = _load_from_neo4j()
                SNAPSHOT.parent.mkdir(exist_ok=True)
                SNAPSHOT.write_text(json.dumps(_base))
            except Exception as e:
                if not SNAPSHOT.exists():
                    raise
                print(f"Neo4j unavailable ({type(e).__name__}); serving the saved snapshot", flush=True)
                _base = json.loads(SNAPSHOT.read_text())
        return _base


def _load_from_neo4j() -> dict:
    customers = {
        r["id"]: {**r, "snapshots": [], "valuations": [], "related": [], "guarantors": [], "events": []}
        for r in run("""
            MATCH (c:Customer)-[:OPERATES_IN]->(i:Industry),
                  (c)-[:IN_SEGMENT]->(s:Segment),
                  (c)-[:BORROWS]->(l:Loan)-[:SECURED_BY]->(k:Collateral)
            RETURN c.id AS id, c.name AS name, c.profile AS profile, c.is_scenario AS is_scenario,
                   toString(c.default_date) AS default_date,
                   toString(c.legacy_flag_date) AS legacy_flag_date,
                   i.id AS industry, s.id AS segment,
                   l.id AS loan_id, l.amount AS loan_amount, l.number AS loan_number,
                   k.id AS collateral_id, k.kind AS collateral_kind""")
    }

    for r in run("""MATCH (c:Customer)-[:HAS_SNAPSHOT]->(s)
                    RETURN c.id AS cid, toString(s.month) AS month, s.revenue AS revenue,
                           s.net_cashflow AS net_cashflow, s.dpd AS dpd ORDER BY s.month"""):
        customers[r.pop("cid")]["snapshots"].append(r)

    for r in run("""MATCH (c:Customer)-[:BORROWS]->()-[:SECURED_BY]->(k)-[:HAS_VALUATION]->(v)
                    RETURN c.id AS cid, toString(v.date) AS date, v.value AS value ORDER BY v.date"""):
        customers[r.pop("cid")]["valuations"].append(r)

    comp_snaps = _by(run("""MATCH (p:Company)-[:HAS_SNAPSHOT]->(s)
                            RETURN p.id AS id, toString(s.month) AS month, s.dpd AS dpd ORDER BY s.month"""), "id")
    for r in run("""MATCH (c:Customer)-[rel:RELATED_TO]->(p:Company)
                    RETURN c.id AS cid, p.id AS id, p.name AS name, toString(rel.valid_from) AS since"""):
        cid = r.pop("cid")
        r["snapshots"] = [{"month": s["month"], "dpd": s["dpd"]} for s in comp_snaps[r["id"]]]
        customers[cid]["related"].append(r)

    for r in run("""MATCH (g:Guarantor)-[:GUARANTEES]->(l:Loan)<-[:BORROWS]-(c:Customer)
                    RETURN c.id AS cid, g.id AS id, g.name AS name"""):
        customers[r.pop("cid")]["guarantors"].append(r)

    trends = {}
    for r in run("""MATCH (i:Industry)-[:HAS_TREND]->(t)
                    RETURN i.id AS ind, toString(t.month) AS month, t.growth_pct AS growth_pct ORDER BY t.month"""):
        trends.setdefault(r["ind"], []).append({"month": r["month"], "growth_pct": r["growth_pct"]})

    events = run("""MATCH (e:RiskEvent)-[:AFFECTS]->(t)
                    RETURN e.id AS id, e.type AS type, toString(e.date) AS date, e.description AS description,
                           t.id AS target_id, labels(t)[0] AS target_type""")
    by_collateral = {c["collateral_id"]: cid for cid, c in customers.items()}
    for e in events:
        if e["target_type"] == "Customer":
            customers[e["target_id"]]["events"].append(e)
        elif e["target_type"] == "Collateral":
            customers[by_collateral[e["target_id"]]]["events"].append(e)
        elif e["target_type"] == "Industry":
            for c in customers.values():
                if c["industry"] == e["target_id"]:
                    c["events"].append(e)

    policies = {r["id"]: r for r in run("MATCH (p:Policy) RETURN p.id AS id, p.title AS title, p.text AS text")}
    return {"customers": customers, "industry_trends": trends, "policies": policies}


_memo: dict = {}


def memo(key, compute):
    """Caches an expensive result computed from the loaded data (a 20k-customer portfolio takes seconds to re-score).
    Cleared whenever the data is reloaded."""
    with _lock:
        if key not in _memo:
            _memo[key] = compute()
        return _memo[key]


def refresh() -> dict:
    """Re-applies the live overlays to the loaded data (no database round trip) and drops every derived cache."""
    global _data
    with _lock:
        _data = None
        _memo.clear()
        contagion._cache.clear()
        return load_all()


def reload():
    global _base
    with _lock:
        _base = None
        return refresh()
