"""Live changes layered on top of the loaded portfolio: news events an officer confirmed and completed mitigation tasks.

The base data (Neo4j or the snapshot) is never modified. Each overlay is stored in data/live/ and re-applied, in order,
whenever the portfolio is (re)loaded, so the rules engine, the contagion engine, time travel and Gemini all see the same
picture. Every effect starts at the overlay's `as_of` month, so earlier months stay as they were. Mechanics (deliberately
simple and documented, like the stress test):

  event on a company   the company is past due from as_of on (15 / 45 / 90 days by severity; bankruptcy is always 90)
  event on a customer  payment problems raise its days past due the same way; revenue loss cuts revenue 15 / 30 / 45% over
                       the last 3 months and turns the last 2 cash flows negative; collateral impairment cuts the valuation
  event on an industry growth is set to -4 / -6 / -9% for the last 2 months
  mitigation           collateral topped up to its recent peak, related past-due companies ring-fenced, or the loan rescheduled
                       (days past due back to 0); monitoring and suspension are recorded but do not change the score
"""
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

LIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "live"

SEV_DPD = {"low": 15, "medium": 45, "high": 90}
SEV_PCT = {"low": 15, "medium": 30, "high": 45}
SEV_GROWTH = {"low": -4.0, "medium": -6.0, "high": -9.0}

EVENT_TYPES = {
    "payment_default": "Missed payment or past due",
    "bankruptcy": "Insolvency or bankruptcy filing",
    "tax_enforcement": "Tax enforcement",
    "legal_action": "Lawsuit or legal action",
    "revenue_loss": "Loss of revenue (lost contract, plant shutdown)",
    "collateral_impairment": "Collateral damaged or devalued",
    "industry_downturn": "Industry downturn",
}
MITIGATIONS = {
    "request_additional_collateral": "Collateral topped up to its peak of the last 6 valuations",
    "review_related_exposure": "Past-due related companies ring-fenced: they no longer feed the rules or the contagion graph",
    "restructure_loan": "Loan rescheduled: days past due back to 0 from this month",
    "suspend_disbursement": "No new disbursement: exposure capped, score unchanged",
    "increase_monitoring": "Monthly review scheduled: score unchanged",
    "no_action": "No change",
}


class JsonList:
    """A tiny thread-safe list of JSON records in one file (one file per data source, so mock and real data never mix)."""

    def __init__(self, name: str):
        self.name = name
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return LIVE_DIR / f"{self.name}_{'real' if os.getenv('DATA_SOURCE') == 'real' else 'mock'}.json"

    def all(self) -> list[dict]:
        with self._lock:
            return json.loads(self.path.read_text()) if self.path.exists() else []

    def save(self, rows: list[dict]):
        with self._lock:
            LIVE_DIR.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(rows, ensure_ascii=False, indent=1))


store = JsonList("overlays")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new(kind: str, as_of: str, **fields) -> dict:
    return {"id": f"{'EV' if kind == 'event' else 'MT'}-{uuid.uuid4().hex[:8]}", "kind": kind, "as_of": as_of[:7] + "-01",
            "created_at": now(), **fields}


def _back(month: str, k: int) -> str:
    y, m = int(month[:4]), int(month[5:7]) - k
    while m <= 0:
        m, y = m + 12, y - 1
    return f"{y:04d}-{m:02d}-01"


def _raise_dpd(snaps: list[dict], as_of: str, dpd: int, key: str = "month") -> list[dict]:
    out = [{**s, "dpd": max(s["dpd"], dpd)} if s[key] >= as_of else s for s in snaps]
    if not any(s[key] == as_of for s in snaps):  # the source has no row for this month yet
        out.append({key: as_of, "dpd": dpd})
        out.sort(key=lambda s: s[key])
    return out


def apply_all(base: dict) -> dict:
    data = base
    for ov in store.all():
        try:
            data = apply_one(data, ov)
        except (KeyError, ValueError) as e:  # an overlay for a customer that no longer exists (data reseeded) is skipped
            print(f"overlay {ov.get('id')} skipped: {type(e).__name__}: {e}", flush=True)
    return data


def apply_one(data: dict, ov: dict) -> dict:
    """Returns a copy of `data` with the overlay applied. Only the touched customers and trends are copied."""
    data = {**data, "customers": dict(data["customers"]), "industry_trends": dict(data["industry_trends"])}
    (_event if ov["kind"] == "event" else _mitigation)(data, ov)
    return data


def _event(data: dict, ov: dict):
    custs, as_of, sev = data["customers"], ov["as_of"], ov.get("severity", "medium")
    kind, target, ttype = ov["event_type"], ov["target_id"], ov["target_type"]
    ev = {"id": ov["id"], "type": kind, "date": as_of, "description": ov["description"], "target_id": target,
          "target_type": ttype, "source": "news"}
    if ttype == "Company":
        dpd = 90 if kind == "bankruptcy" else SEV_DPD[sev]
        hit = False
        for cid, c in custs.items():
            if any(r["id"] == target for r in c["related"]):
                hit = True
                rel = [{**r, "snapshots": _raise_dpd(r["snapshots"], as_of, dpd)} if r["id"] == target else r for r in c["related"]]
                custs[cid] = {**c, "related": rel, "events": c["events"] + [ev]}
        if not hit:
            raise KeyError(target)
    elif ttype == "Industry":
        trends = data["industry_trends"]
        tr = [dict(t) for t in trends[target]]
        idx = [i for i, t in enumerate(tr) if t["month"] <= as_of][-2:] + [i for i, t in enumerate(tr) if t["month"] > as_of]
        for i in idx:
            tr[i]["growth_pct"] = min(tr[i]["growth_pct"], SEV_GROWTH[sev])
        trends[target] = tr
        for cid, c in custs.items():
            if c["industry"] == target:
                custs[cid] = {**c, "events": c["events"] + [ev]}
    else:
        c = custs[target]
        c = {**c, "events": c["events"] + [{**ev, "target_type": "Customer"}]}
        if kind in ("revenue_loss", "industry_downturn"):
            pct = SEV_PCT[sev] / 100
            snaps = []
            for s in c["snapshots"]:
                if s["month"] >= _back(as_of, 2):
                    rev = s["revenue"]
                    s = {**s, "revenue": round(rev * (1 - pct), 1)}
                    if s["month"] >= _back(as_of, 1):
                        net = s["net_cashflow"] - rev * pct / 2
                        s["net_cashflow"] = round(min(net, -0.02 * rev) if sev != "low" else net, 1)
                snaps.append(s)
            c["snapshots"] = snaps
        elif kind == "collateral_impairment":
            pct = SEV_PCT[sev] / 100
            vals = [dict(v) for v in c["valuations"]]
            if not any(v["date"] == as_of for v in vals):
                prior = [v for v in vals if v["date"] <= as_of]
                if prior:
                    vals.append({"date": as_of, "value": prior[-1]["value"]})
                    vals.sort(key=lambda v: v["date"])
            for v in vals:
                if v["date"] >= as_of:
                    v["value"] = round(v["value"] * (1 - pct), 1)
            c["valuations"] = vals
        else:  # payment_default, bankruptcy, tax_enforcement, legal_action
            c["snapshots"] = _raise_dpd(c["snapshots"], as_of, 90 if kind == "bankruptcy" else SEV_DPD[sev])
            c["snapshots"] = [s if "revenue" in s else _fill(s, c["snapshots"]) for s in c["snapshots"]]
        custs[target] = c


def _fill(row: dict, snaps: list[dict]) -> dict:
    """A month added by `_raise_dpd` to a customer copies the figures of the previous month."""
    prev = [s for s in snaps if s["month"] < row["month"] and "revenue" in s]
    return {**(prev[-1] if prev else {"revenue": 0, "net_cashflow": 0}), **row}


def _mitigation(data: dict, ov: dict):
    custs, as_of, action = data["customers"], ov["as_of"], ov["action"]
    c = dict(custs[ov["customer_id"]])
    effect = MITIGATIONS.get(action, "No change")
    if action == "request_additional_collateral":
        vals = [dict(v) for v in c["valuations"]]
        prior = [v for v in vals if v["date"] <= as_of][-6:]
        if prior:
            peak = max(v["value"] for v in prior)
            if not any(v["date"] == as_of for v in vals):
                vals.append({"date": as_of, "value": peak})
                vals.sort(key=lambda v: v["date"])
            for v in vals:
                if v["date"] >= as_of:
                    v["value"] = max(v["value"], peak)
            c["valuations"] = vals
    elif action == "review_related_exposure":
        fenced = []
        rel = []
        for r in c["related"]:
            dpd = next((s["dpd"] for s in reversed(r["snapshots"]) if s["month"] <= as_of), 0)
            if dpd > 0 and r.get("until", "9999-12-31") > as_of:
                r = {**r, "until": as_of}
                fenced.append(r["name"])
            rel.append(r)
        c["related"] = rel
        effect = f"Ring-fenced {', '.join(fenced)}" if fenced else "No past-due related company to ring-fence"
    elif action == "restructure_loan":
        c["snapshots"] = [{**s, "dpd": 0} if s["month"] >= as_of else s for s in c["snapshots"]]
    c["mitigations"] = c.get("mitigations", []) + [{"id": ov["id"], "task_id": ov.get("task_id"), "action": action,
                                                     "as_of": as_of, "effect": effect}]
    custs[ov["customer_id"]] = c


def add(ov: dict) -> dict:
    rows = store.all()
    rows.append(ov)
    store.save(rows)
    return ov


def remove(overlay_id: str) -> bool:
    rows = store.all()
    keep = [r for r in rows if r["id"] != overlay_id]
    store.save(keep)
    return len(keep) != len(rows)


def clear():
    store.save([])
