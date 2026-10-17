"""Context Builder: builds the Context Graph (the subgraph relevant to one decision) as of a given month."""
from app.scoring import active, evaluate, month_floor


def build_context(history: dict, customer_id: str, as_of: str) -> dict:
    as_of = month_floor(as_of)
    c = history["customers"][customer_id]
    result = evaluate(c, history["industry_trends"], as_of)
    flagged = {e for s in result["signals"] for e in s["evidence"]}

    snaps = [s for s in c["snapshots"] if s["month"] <= as_of]
    latest = snaps[-1] if snaps else {}
    nodes = [
        {"id": c["id"], "type": "Customer", "label": c["name"], "props": {
            "segment": c["segment"], "revenue": latest.get("revenue"),
            "net_cashflow": latest.get("net_cashflow"), "dpd": latest.get("dpd")}},
        {"id": c["loan_id"], "type": "Loan",
         "label": f"Loan #{c['loan_number']}" if c["loan_number"] else f"Loan {c['loan_id']}",
         "props": {"amount": c["loan_amount"], "dpd": latest.get("dpd")}},
        {"id": c["collateral_id"], "type": "Collateral", "label": f"Collateral ({c['collateral_kind']})", "props": {
            "value": next((v["value"] for v in reversed(c["valuations"]) if v["date"] <= as_of), None)}},
        {"id": c["industry"], "type": "Industry", "label": f"{c['industry']} industry", "props": {
            "growth_pct": next((t["growth_pct"] for t in reversed(history["industry_trends"][c["industry"]])
                                if t["month"] <= as_of), None)}},
    ]
    edges = [
        {"source": c["id"], "target": c["loan_id"], "type": "BORROWS"},
        {"source": c["loan_id"], "target": c["collateral_id"], "type": "SECURED_BY"},
        {"source": c["id"], "target": c["industry"], "type": "OPERATES_IN"},
    ]
    for rel in c["related"]:
        if rel["since"] <= as_of:
            dpd = next((s["dpd"] for s in reversed(rel["snapshots"]) if s["month"] <= as_of), 0)
            fenced = not active(rel, as_of)
            nodes.append({"id": rel["id"], "type": "Company", "label": rel["name"], "props": {"dpd": dpd, "ring_fenced": fenced}})
            edges.append({"source": c["id"], "target": rel["id"], "type": "RING_FENCED" if fenced else "RELATED_TO"})
    for g in c["guarantors"]:
        nodes.append({"id": g["id"], "type": "Guarantor", "label": g["name"], "props": {}})
        edges.append({"source": g["id"], "target": c["loan_id"], "type": "GUARANTEES"})
    events = [e for e in c["events"] if e["date"] <= as_of]
    for e in events:
        nodes.append({"id": e["id"], "type": "RiskEvent", "label": e["description"], "props": {"date": e["date"]}})
        target = e["target_id"] if e["target_type"] != "Collateral" else c["collateral_id"]
        edges.append({"source": e["id"], "target": target if e["target_type"] != "Customer" else c["id"],
                      "type": "AFFECTS"})

    for n in nodes:
        n["flagged"] = n["id"] in flagged
    policies = {s["policy"] for s in result["signals"]}
    return {
        "customer": {k: c[k] for k in ("id", "name", "industry", "segment", "loan_amount", "default_date", "legacy_flag_date")},
        "as_of": as_of,
        "assessment": result,
        "graph": {"nodes": nodes, "edges": edges},
        "events": sorted(events, key=lambda e: e["date"]),
        "mitigations": [m for m in c.get("mitigations", []) if m["as_of"] <= as_of],
        "policies": [history["policies"][p] for p in sorted(policies) if p in history["policies"]],
        "history": {"snapshots": snaps, "valuations": [v for v in c["valuations"] if v["date"] <= as_of]},
    }
