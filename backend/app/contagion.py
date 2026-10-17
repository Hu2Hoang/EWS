"""Contagion engine: spreads risk along the relationship graph around each customer.

Like scoring, it is deterministic (the LLM takes no part). It works on any snapshot, mock or real, because it only needs the
relations already in the history:
  customer -[related]- company         customer["related"]   (weight = share of the relationship, e.g. transaction value)
  customer -[guarantor]- guarantor     customer["guarantors"]
  customer -[industry]- industry       customer["industry"]
  customer -[transaction]- customer    history["edges"]      (optional, {source, target, type, weight, since})
A relation only counts from its `since` month, so the graph at `as_of` never sees the future.

Own risk (0..1) of a node:  customer = rule score / 100, company = days past due / 90, industry = how far growth is below zero.
Risk that reaches customer i from its neighbours j over paths of up to `hops` edges:

    inflow_i = sum over non-backtracking paths  alpha^len * prod(type_weight * share) * own_end

`share` is the edge weight divided by the receiver's total weight of that relation type, so a node with many neighbours dilutes each of
them. A path never returns to the customer it started from, so a customer's own risk is not counted as contagion.
"""
from collections import defaultdict, deque

from app.scoring import active, evaluate, month_floor

TYPE_WEIGHTS = {"related": 0.6, "guarantor": 0.5, "transaction": 0.4, "industry": 0.15}
DEFAULT_TYPE_WEIGHT = 0.3   # relation types this engine does not know yet (so new edge tables work without code changes)
ALPHA, HOPS, WATCH = 0.5, 2, 0.10
MAX_HOPS = 3                # the walk visits every non-backtracking path, so deeper is slow
DEFAULT_AT = 0.8            # cascade: a borrower pushed by a shock from below this combined risk to at or above it defaults next round
MAX_ROUNDS = 4
HUB_LIMIT = 100             # a node with more neighbours of one relation type (an industry, a payment processor) is a hub:
                            # it passes on the weighted average of its members' risk in one step instead of being walked member by member
_cache: dict = {}


def _company_risk(rel: dict, as_of: str) -> float:
    dpd = next((s["dpd"] for s in reversed(rel["snapshots"]) if s["month"] <= as_of), 0)
    return 0.0 if dpd <= 0 else min(1.0, max(0.2, dpd / 90))


def _industry_risk(trends: dict, industry: str, as_of: str) -> float:
    growth = next((t["growth_pct"] for t in reversed(trends.get(industry, [])) if t["month"] <= as_of), 0.0)
    return min(1.0, max(0.0, -growth / 10))


def build_graph(history: dict, as_of: str, overrides: dict | None = None) -> tuple[dict, dict]:
    """nodes: id -> {type, label, own};  inbound: receiver -> relation type -> [(sender, weight)] (every relation is two-way).
    `overrides` (node id -> own risk) replaces the own risk of some nodes, e.g. 1.0 for a node that defaults in a simulation."""
    as_of = month_floor(as_of)
    trends = history["industry_trends"]
    nodes: dict = {}
    inbound: dict = defaultdict(lambda: defaultdict(list))

    def link(a, b, kind, weight=1.0):
        weight = max(float(weight or 0), 1e-9)
        inbound[a][kind].append((b, weight))
        inbound[b][kind].append((a, weight))

    for cid, c in history["customers"].items():
        nodes[cid] = {"type": "Customer", "label": c["name"], "own": evaluate(c, trends, as_of)["score"] / 100}
    for cid, c in history["customers"].items():
        industry = c["industry"]
        nodes.setdefault(industry, {"type": "Industry", "label": industry, "own": _industry_risk(trends, industry, as_of)})
        link(cid, industry, "industry")
        for rel in c["related"]:
            if rel["since"] <= as_of and active(rel, as_of):
                nodes.setdefault(rel["id"], {"type": "Company", "label": rel["name"], "own": _company_risk(rel, as_of)})
                link(cid, rel["id"], "related", rel.get("weight", 1.0))
        for g in c["guarantors"]:
            nodes.setdefault(g["id"], {"type": "Guarantor", "label": g["name"], "own": 0.0})
            link(cid, g["id"], "guarantor")
    for e in history.get("edges", []):
        if e["since"] <= as_of and e["source"] in nodes and e["target"] in nodes:
            link(e["source"], e["target"], e.get("type", "transaction"), e.get("weight", 1.0))
    for n, own in (overrides or {}).items():
        if n in nodes:
            nodes[n]["own"] = own
    return nodes, inbound


def propagate(history: dict, as_of: str, alpha: float = ALPHA, hops: int = HOPS, type_weights: dict | None = None,
              top: int = 5) -> dict:
    """customer id -> {own, inflow, total, contagion_score, watch, sources: [...]} (as_of month, no look-ahead). Results are cached."""
    hops = max(1, min(int(hops), MAX_HOPS))
    tw = {**TYPE_WEIGHTS, **(type_weights or {})}
    key = (id(history), month_floor(as_of), alpha, hops, tuple(sorted(tw.items())), top)
    if key not in _cache:
        if len(_cache) >= 8:
            _cache.pop(next(iter(_cache)))
        _cache[key] = _propagate(history, month_floor(as_of), alpha, hops, tw, top)
    return _cache[key]


def compute(history: dict, as_of: str, alpha: float = ALPHA, hops: int = HOPS, overrides: dict | None = None, top: int = 5) -> dict:
    """Same as `propagate` but never cached: for simulated or temporary copies of the portfolio."""
    return _propagate(history, month_floor(as_of), alpha, max(1, min(int(hops), MAX_HOPS)), TYPE_WEIGHTS, top, overrides)


def _propagate(history, as_of, alpha, hops, tw, top, overrides=None):
    nodes, inbound = build_graph(history, as_of, overrides)
    totals = {n: {k: sum(w for _, w in nbrs) for k, nbrs in by_kind.items()} for n, by_kind in inbound.items()}
    hubs = {n: {k: ({s: w for s, w in nbrs}, sum(w * nodes[s]["own"] for s, w in nbrs), len(nbrs))
                for k, nbrs in by_kind.items() if len(nbrs) > HUB_LIMIT} for n, by_kind in inbound.items()}

    def walk(node, came_from, depth, mult, path, acc):
        for kind, nbrs in inbound[node].items():
            factor = alpha * tw.get(kind, DEFAULT_TYPE_WEIGHT) / totals[node][kind]
            if kind in hubs[node]:
                weights, weighted_own, count = hubs[node][kind]
                came_w = weights.get(came_from, 0.0)
                val = mult * factor * (weighted_own - came_w * (nodes[came_from]["own"] if came_from else 0.0))
                if val > 1e-12:
                    peers = f"{node}:peers"
                    acc[peers] = acc.get(peers, 0.0) + val
                    if val > best_path.get(peers, (0,))[0]:
                        best_path[peers] = (val, path + (peers,), kind)
                    peer_info[peers] = (node, count)
                continue
            for sender, w in nbrs:
                if sender == came_from or sender in path:
                    continue
                m = mult * factor * w
                own = nodes[sender]["own"]
                if own > 0:
                    acc[sender] = acc.get(sender, 0.0) + m * own
                    best = best_path.setdefault(sender, (m * own, path + (sender,), kind))
                    if m * own > best[0]:
                        best_path[sender] = (m * own, path + (sender,), kind)
                if depth < hops:
                    walk(sender, node, depth + 1, m, path + (sender,), acc)

    peer_info: dict = {}
    out = {}
    for cid, node in nodes.items():
        if node["type"] != "Customer":
            continue
        best_path: dict = {}
        acc: dict = {}
        walk(cid, None, 1, 1.0, (cid,), acc)
        inflow = min(1.0, sum(acc.values()))
        sources = [{"id": s, "label": nodes[s]["label"] if s in nodes else f"Peers in {peer_info[s][0]} ({peer_info[s][1]})",
                    "type": nodes[s]["type"] if s in nodes else "Peers", "own": round(nodes[s]["own"], 2) if s in nodes else None,
                    "relation": best_path[s][2], "via": list(best_path[s][1][1:-1]), "hops": len(best_path[s][1]) - 1,
                    "contribution": round(v, 4)} for s, v in sorted(acc.items(), key=lambda kv: -kv[1])[:top]]
        out[cid] = {"own": round(node["own"], 3), "inflow": round(inflow, 4), "total": round(min(1.0, node["own"] + inflow), 3),
                    "contagion_score": round(inflow * 100, 1), "watch": inflow >= WATCH, "sources": sources}
    return out


def _adjacency(inbound: dict) -> dict:
    """Plain neighbour lists without the industry relation (an industry links everyone, so it would make every path two hops)."""
    adj: dict = defaultdict(list)
    for n, by_kind in inbound.items():
        for kind, nbrs in by_kind.items():
            if kind != "industry":
                adj[n].extend((s, kind) for s, _ in nbrs)
    return adj


def shock(history: dict, as_of: str, seeds: list[str], alpha: float = ALPHA, hops: int = HOPS,
          threshold: float = DEFAULT_AT, min_delta: float = 0.01, limit: int = 60) -> dict:
    """Default cascade: the seed nodes default (own risk 1.0) and the risk spreads along the graph. A borrower whose combined
    risk (own + inflow) the shock pushes from below `threshold` to at or above it defaults in the next round, and spreads in
    turn. Stops when a round adds no default (at most MAX_ROUNDS). A failed company also feeds the rules (45+ days past due
    is what the stress test uses; a default here is 90), so rule levels move too. Nothing is written."""
    as_of = month_floor(as_of)
    nodes, inbound = build_graph(history, as_of)
    seeds = [s for s in dict.fromkeys(seeds) if s in nodes and nodes[s]["type"] != "Industry"]
    if not seeds:
        raise ValueError("Pick a company, customer or guarantor that exists at this date")
    custs = history["customers"]
    before = propagate(history, as_of, alpha, hops)
    defaulted = {s: 0 for s in seeds}
    first_hit: dict = {}
    after = before
    for rnd in range(1, MAX_ROUNDS + 1):
        after = compute(history, as_of, alpha, hops, {n: 1.0 for n in defaulted})
        for cid, v in after.items():
            if cid not in first_hit and v["inflow"] - before[cid]["inflow"] >= min_delta:
                first_hit[cid] = rnd
        new = [cid for cid, v in after.items() if cid not in defaulted and before[cid]["total"] < threshold <= v["total"]]
        for cid in new:
            defaulted[cid] = rnd
        if not new:
            break

    failed = {s for s in seeds if nodes[s]["type"] == "Company"}
    trends = history["industry_trends"]

    def rules(c: dict) -> tuple[str, str]:
        b = evaluate(c, trends, as_of)["level"]
        if not any(r["id"] in failed for r in c["related"]):
            return b, b
        rel = [{**r, "snapshots": [{**s, "dpd": max(s["dpd"], 90)} if s["month"] >= as_of else s for s in r["snapshots"]]
                                   + ([] if any(s["month"] == as_of for s in r["snapshots"]) else [{"month": as_of, "dpd": 90}])}
               if r["id"] in failed else r for r in c["related"]]
        return b, evaluate({**c, "related": rel}, trends, as_of)["level"]

    adj = _adjacency(inbound)
    dist, parent = {s: 0 for s in seeds}, {}
    queue = deque(seeds)
    while queue:
        n = queue.popleft()
        for m, kind in adj[n]:
            if m not in dist:
                dist[m], parent[m] = dist[n] + 1, (n, kind)
                queue.append(m)

    rank = {"Low": 0, "Medium": 1, "High": 2}
    affected = []
    for cid, v in after.items():
        if cid in seeds:
            continue
        delta = v["inflow"] - before[cid]["inflow"]
        lb, la = rules(custs[cid])
        if delta < min_delta and cid not in defaulted and la == lb:
            continue
        c = custs[cid]
        affected.append({"id": cid, "name": c["name"], "industry": c["industry"], "loan_amount": c["loan_amount"],
                         "hop": dist.get(cid), "round": defaulted.get(cid, first_hit.get(cid)), "defaulted": cid in defaulted,
                         "inflow_before": before[cid]["inflow"], "inflow_after": v["inflow"], "delta": round(delta, 4),
                         "total_before": before[cid]["total"], "total_after": 1.0 if cid in defaulted else v["total"],
                         "watch_before": before[cid]["watch"], "watch_after": v["watch"],
                         "level_before": lb, "level_after": la})
    affected.sort(key=lambda r: (not r["defaulted"], r["round"] or 9, -r["delta"]))

    # Subgraph for the UI: seeds, every cascade default and the most affected borrowers, joined by their shortest path.
    show = [a["id"] for a in affected if a["defaulted"]] + [a["id"] for a in sorted(affected, key=lambda r: -r["delta"])
                                                            if not a["defaulted"]][:limit]
    by_id = {a["id"]: a for a in affected}
    g_nodes, g_edges, seen = {}, {}, set()

    def add_node(n):
        if n in g_nodes:
            return
        a = by_id.get(n)
        state = "seed" if n in seeds else "default" if n in defaulted else "hit" if a else "path"
        g_nodes[n] = {"id": n, "type": nodes[n]["type"], "label": nodes[n]["label"], "state": state, "hop": dist.get(n),
                      "round": defaulted.get(n, a["round"] if a else None), "delta": a["delta"] if a else None,
                      "own": round(nodes[n]["own"], 2)}

    for n in seeds + show:
        if n not in dist or n in seen:
            continue
        add_node(n)
        while n in parent and n not in seen:
            seen.add(n)
            p, kind = parent[n]
            add_node(p)
            g_edges[(p, n)] = {"source": p, "target": n, "type": kind}
            n = p

    seed_rows = [{"id": s, "label": nodes[s]["label"], "type": nodes[s]["type"], "own_before": round(nodes[s]["own"], 2),
                  "loan_amount": custs[s]["loan_amount"] if s in custs else None} for s in seeds]
    cascade = [a for a in affected if a["defaulted"]]
    return {
        "as_of": as_of, "alpha": alpha, "hops": hops, "threshold": threshold, "seeds": seed_rows,
        "rounds": [{"round": r, "defaults": [{"id": n, "name": nodes[n]["label"]} for n, rr in defaulted.items() if rr == r]}
                   for r in sorted(set(defaulted.values()))],
        "summary": {
            "affected": len(affected), "cascade_defaults": len(cascade),
            "new_watch": sum(1 for a in affected if a["watch_after"] and not a["watch_before"]),
            "level_ups": sum(1 for a in affected if rank[a["level_after"]] > rank[a["level_before"]]),
            "exposure_default": round(sum(s["loan_amount"] or 0 for s in seed_rows) + sum(a["loan_amount"] for a in cascade)),
            "exposure_affected": round(sum(a["loan_amount"] for a in affected)),
        },
        "affected": affected[:200],
        "graph": {"nodes": list(g_nodes.values()), "edges": list(g_edges.values())},
    }


def matrix(history: dict, as_of: str, alpha: float = ALPHA, hops: int = HOPS, rows: int = 12) -> dict:
    """Contagion matrix: who spreads risk (rows) into which industry (columns). A cell is the risk (in points, inflow x 100)
    a source passes to borrowers of that industry, with how many borrowers it reaches. A second view splits the same
    inflow by channel (related company, guarantor, transaction, industry co-movement)."""
    res = propagate(history, as_of, alpha, hops, top=50)
    custs = history["customers"]
    industries = sorted(history["industry_trends"])
    blank = lambda: {i: {"value": 0.0, "count": 0} for i in industries}
    spreaders: dict = {}
    channels: dict = {}
    totals = blank()
    for cid, v in res.items():
        ind = custs[cid]["industry"]
        for s in v["sources"]:
            val = s["contribution"] * 100
            for cell in (totals[ind], channels.setdefault(s["relation"], blank())[ind]):
                cell["value"] += val
                cell["count"] += 1
            if s["type"] in ("Industry", "Peers"):
                continue
            row = spreaders.setdefault(s["id"], {"id": s["id"], "label": s["label"], "type": s["type"], "own": s["own"],
                                                 "total": 0.0, "reach": 0, "cells": blank()})
            row["total"] += val
            row["reach"] += 1
            row["cells"][ind]["value"] += val
            row["cells"][ind]["count"] += 1

    def out(cells):
        return [{"industry": i, "value": round(cells[i]["value"], 2), "count": cells[i]["count"]} for i in industries]

    top = sorted(spreaders.values(), key=lambda r: -r["total"])[:rows]
    return {
        "as_of": month_floor(as_of), "alpha": alpha, "hops": hops, "industries": industries,
        "rows": [{**{k: r[k] for k in ("id", "label", "type", "own", "reach")}, "total": round(r["total"], 2), "cells": out(r["cells"])}
                 for r in top],
        "channels": [{"relation": k, "total": round(sum(c["value"] for c in v.values()), 2), "cells": out(v)}
                     for k, v in sorted(channels.items(), key=lambda kv: -sum(c["value"] for c in kv[1].values()))],
        "totals": out(totals),
        "max_cell": round(max((c["value"] for r in top for c in r["cells"].values()), default=0), 2),
    }


def receivers(history: dict, as_of: str, source: str, alpha: float = ALPHA, hops: int = HOPS) -> list[dict]:
    """Borrowers that currently take risk from one source node (a row of the matrix), strongest first."""
    custs = history["customers"]
    rows = [{"id": cid, "name": custs[cid]["name"], "industry": custs[cid]["industry"], "loan_amount": custs[cid]["loan_amount"],
             "contribution": round(s["contribution"] * 100, 2), "relation": s["relation"], "hops": s["hops"],
             "inflow": v["inflow"], "own": v["own"]}
            for cid, v in propagate(history, as_of, alpha, hops, top=50).items() for s in v["sources"] if s["id"] == source]
    return sorted(rows, key=lambda r: -r["contribution"])
