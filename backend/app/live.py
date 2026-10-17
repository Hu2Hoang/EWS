"""Closes the loop: alert -> action -> effect. Credit officers turn proposals into tasks, completing a task applies its
mitigation as a live overlay, and confirmed news events do the same for new risk. Every change reports its impact
(rule levels before/after, contagion before/after), computed by the same deterministic engines as everything else.
"""
from datetime import datetime, timedelta, timezone
import uuid

from app import contagion, overlays, repository
from app.overlays import EVENT_TYPES, MITIGATIONS, JsonList
from app.scoring import evaluate, month_floor

RANK = {"Low": 0, "Medium": 1, "High": 2}
SLA_DAYS = {"suspend_disbursement": 1, "review_related_exposure": 7, "request_additional_collateral": 14,
            "restructure_loan": 30, "increase_monitoring": 30, "no_action": 0}
STATUSES = ("todo", "doing", "done")
tasks = JsonList("tasks")


def impact(before: dict, after: dict, as_of: str, focus: str | None = None) -> dict:
    """What changes between two versions of the portfolio at one month. `before` must be the live data (its contagion is cached)."""
    as_of = month_floor(as_of)
    tb, ta = before["industry_trends"], after["industry_trends"]
    levels = []
    for cid, c in after["customers"].items():
        cb = before["customers"][cid]
        if cb is c and tb[c["industry"]] is ta[c["industry"]]:
            continue
        b, a = evaluate(cb, tb, as_of), evaluate(c, ta, as_of)
        if (b["score"], b["level"]) == (a["score"], a["level"]):
            continue
        sb, sa = {s["code"] for s in b["signals"]}, {s["code"] for s in a["signals"]}
        levels.append({"id": cid, "name": c["name"], "industry": c["industry"], "loan_amount": c["loan_amount"],
                       "level_before": b["level"], "level_after": a["level"], "score_before": b["score"], "score_after": a["score"],
                       "added_signals": sorted(sa - sb), "removed_signals": sorted(sb - sa)})
    levels.sort(key=lambda r: (-(RANK[r["level_after"]] - RANK[r["level_before"]]), -abs(r["score_after"] - r["score_before"]), -r["loan_amount"]))

    pb, pa = contagion.propagate(before, as_of), contagion.compute(after, as_of)
    spread = []
    for cid, v in pa.items():
        d = v["inflow"] - pb[cid]["inflow"]
        if abs(d) >= 0.01 or v["watch"] != pb[cid]["watch"]:
            spread.append({"id": cid, "name": after["customers"][cid]["name"], "inflow_before": pb[cid]["inflow"],
                           "inflow_after": v["inflow"], "delta": round(d, 4), "watch_before": pb[cid]["watch"], "watch_after": v["watch"]})
    spread.sort(key=lambda r: -abs(r["delta"]))

    high = lambda r, k: after["customers"][r["id"]]["loan_amount"] if r[k] == "High" else 0
    out = {
        "as_of": as_of,
        "summary": {
            "level_up": sum(RANK[r["level_after"]] > RANK[r["level_before"]] for r in levels),
            "level_down": sum(RANK[r["level_after"]] < RANK[r["level_before"]] for r in levels),
            "score_changed": len(levels),
            "newly_watch": sum(r["watch_after"] and not r["watch_before"] for r in spread),
            "watch_cleared": sum(r["watch_before"] and not r["watch_after"] for r in spread),
            "contagion_changed": len(spread),
            "exposure_high_delta": round(sum(high(r, "level_after") - high(r, "level_before") for r in levels)),
        },
        "level_changes": levels[:30],
        "contagion_changes": spread[:20],
    }
    if focus:
        b, a = evaluate(before["customers"][focus], tb, as_of), evaluate(after["customers"][focus], ta, as_of)
        sb, sa = {s["code"] for s in b["signals"]}, {s["code"] for s in a["signals"]}
        out["focus"] = {"level_before": b["level"], "level_after": a["level"], "score_before": b["score"], "score_after": a["score"],
                        "inflow_before": pb[focus]["inflow"], "inflow_after": pa[focus]["inflow"],
                        "removed_signals": sorted(sb - sa), "added_signals": sorted(sa - sb),
                        "neighbours_relieved": sum(1 for r in spread if r["id"] != focus and r["delta"] < 0)}
    return out


def preview(ov: dict) -> dict:
    live = repository.load_all()
    return impact(live, overlays.apply_one(live, ov), ov["as_of"], ov.get("customer_id"))


def apply(ov: dict) -> dict:
    """Stores the overlay and makes it live. The impact is computed first, against the state it changes."""
    result = preview(ov)
    overlays.add(ov)
    repository.refresh()
    return {"overlay": ov, "impact": result}


def remove(overlay_id: str) -> bool:
    ok = overlays.remove(overlay_id)
    rows = tasks.all()
    for t in rows:
        if t.get("overlay_id") == overlay_id:  # undoing a mitigation reopens its task
            t.update(status="doing", overlay_id=None, result=None)
    tasks.save(rows)
    repository.refresh()
    return ok


def events() -> list[dict]:
    return [o for o in reversed(overlays.store.all()) if o["kind"] == "event"]


def new_event(as_of: str, target_type: str, target_id: str, event_type: str, severity: str, description: str,
              source: str = "manual", text: str = "") -> dict:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown event type {event_type}")
    return overlays.new("event", as_of, target_type=target_type, target_id=target_id, event_type=event_type,
                        severity=severity if severity in overlays.SEV_DPD else "medium", description=description,
                        source=source, text=text[:600])


# ---- Tasks -------------------------------------------------------------------------------------------------------

def _mitigation(t: dict) -> dict:
    return overlays.new("mitigation", t["as_of"], customer_id=t["customer_id"], action=t["action"], task_id=t["id"])


def list_tasks(customer_id: str | None = None) -> list[dict]:
    rows = [t for t in tasks.all() if not customer_id or t["customer_id"] == customer_id]
    return sorted(rows, key=lambda t: t["created_at"], reverse=True)


def create_task(customer_id: str, action: str, as_of: str, note: str = "", officer: str = "Credit Officer",
                source: str = "manual", decision_id: str | None = None) -> dict:
    if action not in MITIGATIONS:
        raise ValueError(f"Unknown action {action}")
    c = repository.load_all()["customers"][customer_id]
    created = datetime.now(timezone.utc)
    t = {"id": f"T-{uuid.uuid4().hex[:6]}", "customer_id": customer_id, "customer_name": c["name"], "industry": c["industry"],
         "loan_amount": c["loan_amount"], "action": action, "as_of": month_floor(as_of), "status": "todo",
         "officer": officer, "note": note, "source": source, "decision_id": decision_id,
         "created_at": created.isoformat(timespec="seconds"), "sla_days": SLA_DAYS.get(action, 7),
         "due": (created + timedelta(days=SLA_DAYS.get(action, 7))).isoformat(timespec="seconds"),
         "effect": MITIGATIONS[action], "overlay_id": None, "result": None,
         "history": [{"status": "todo", "at": created.isoformat(timespec="seconds")}]}
    t["expected"] = preview(_mitigation(t))["focus"]
    tasks.save(tasks.all() + [t])
    return t


def set_status(task_id: str, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"Unknown status {status}")
    rows = tasks.all()
    t = next((r for r in rows if r["id"] == task_id), None)
    if t is None:
        raise KeyError(task_id)
    if status == "done" and t["status"] != "done":
        res = apply(_mitigation(t))
        t["overlay_id"] = res["overlay"]["id"]
        t["result"] = {**res["impact"]["focus"], "summary": res["impact"]["summary"],
                       "effect": repository.load_all()["customers"][t["customer_id"]]["mitigations"][-1]["effect"]}
    elif t["status"] == "done" and status != "done" and t.get("overlay_id"):
        overlays.remove(t["overlay_id"])
        t.update(overlay_id=None, result=None)
        repository.refresh()
    t["status"] = status
    t["history"].append({"status": status, "at": overlays.now()})
    tasks.save(rows)
    return t


def reset():
    overlays.clear()
    tasks.save([])
    repository.refresh()
