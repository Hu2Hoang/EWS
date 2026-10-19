"""Credit-officer decisions (human-in-the-loop) and the audit log, stored in Neo4j.

If Neo4j is unavailable, a decision is kept in a local JSON file instead (data/live/, like the tasks), and the audit log
shows both, so approving an action still works while the database is paused or turned off."""
import uuid

from app.db import run
from app.overlays import JsonList, now

ACTIONS = {"increase_monitoring", "request_additional_collateral", "suspend_disbursement",
           "review_related_exposure", "restructure_loan", "no_action"}
local = JsonList("decisions")


def record(customer_id: str, as_of: str, action: str, decision: str, note: str, officer: str,
           ai_source: str | None, risk_level: str, score: int, customer_name: str = "") -> dict:
    row = {"id": str(uuid.uuid4()), "customer_id": customer_id, "as_of": as_of, "action": action,
           "decision": decision, "note": note, "officer": officer, "ai_source": ai_source,
           "risk_level": risk_level, "score": score}
    try:
        run("""MATCH (c:Customer {id: $customer_id})
               CREATE (d:Decision {id: $id, as_of: date($as_of), action: $action, decision: $decision, note: $note,
                                   officer: $officer, ai_source: $ai_source, risk_level: $risk_level, score: $score,
                                   created_at: datetime()})
               CREATE (d)-[:ON]->(c)""", **row)
        return dict(row)
    except Exception as e:  # database paused or turned off: keep the audit trail locally
        print(f"decision kept locally ({type(e).__name__})", flush=True)
        stored = {**row, "customer_name": customer_name, "created_at": now(), "store": "local"}
        local.save(local.all() + [stored])
        return stored


def list_decisions(customer_id: str | None = None, limit: int = 50) -> list[dict]:
    rows = [r for r in local.all() if customer_id is None or r["customer_id"] == customer_id]
    try:
        rows += run("""MATCH (d:Decision)-[:ON]->(c:Customer)
                       WHERE $cid IS NULL OR c.id = $cid
                       RETURN d.id AS id, c.id AS customer_id, c.name AS customer_name, toString(d.as_of) AS as_of,
                              d.action AS action, d.decision AS decision, d.note AS note, d.officer AS officer,
                              d.ai_source AS ai_source, d.risk_level AS risk_level, d.score AS score,
                              toString(d.created_at) AS created_at
                       ORDER BY d.created_at DESC LIMIT $limit""", cid=customer_id, limit=limit)
    except Exception:
        pass  # Neo4j unavailable: the local records are the audit log for now
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)[:limit]
