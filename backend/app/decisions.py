"""Credit-officer decisions (human-in-the-loop) and the audit log, stored in Neo4j."""
import uuid

from app.db import run

ACTIONS = {"increase_monitoring", "request_additional_collateral", "suspend_disbursement",
           "review_related_exposure", "restructure_loan", "no_action"}


def record(customer_id: str, as_of: str, action: str, decision: str, note: str, officer: str,
           ai_source: str | None, risk_level: str, score: int) -> dict:
    row = {"id": str(uuid.uuid4()), "customer_id": customer_id, "as_of": as_of, "action": action,
           "decision": decision, "note": note, "officer": officer, "ai_source": ai_source,
           "risk_level": risk_level, "score": score}
    run("""MATCH (c:Customer {id: $customer_id})
           CREATE (d:Decision {id: $id, as_of: date($as_of), action: $action, decision: $decision, note: $note,
                               officer: $officer, ai_source: $ai_source, risk_level: $risk_level, score: $score,
                               created_at: datetime()})
           CREATE (d)-[:ON]->(c)""", **row)
    return {k: v for k, v in row.items()}


def list_decisions(customer_id: str | None = None, limit: int = 50) -> list[dict]:
    return run("""MATCH (d:Decision)-[:ON]->(c:Customer)
                  WHERE $cid IS NULL OR c.id = $cid
                  RETURN d.id AS id, c.id AS customer_id, c.name AS customer_name, toString(d.as_of) AS as_of,
                         d.action AS action, d.decision AS decision, d.note AS note, d.officer AS officer,
                         d.ai_source AS ai_source, d.risk_level AS risk_level, d.score AS score,
                         toString(d.created_at) AS created_at
                  ORDER BY d.created_at DESC LIMIT $limit""", cid=customer_id, limit=limit)
