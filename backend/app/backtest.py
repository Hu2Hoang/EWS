"""Compares the early-warning system with the legacy rule (DPD >= 30) on labelled synthetic data."""
from datetime import date

from app.scoring import evaluate, months_of


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def timeline(cust: dict, trends: dict) -> list[dict]:
    out = []
    for m in months_of(cust):
        r = evaluate(cust, trends, m)
        out.append({"month": m, "score": r["score"], "level": r["level"],
                    "signal_count": r["signal_count"], "legacy_flag": r["legacy_flag"]})
    return out


def run_backtest(history: dict) -> dict:
    rows, trends = [], history["industry_trends"]
    for cid, c in history["customers"].items():
        tl = timeline(c, trends)
        ew = next((t["month"] for t in tl if t["level"] == "High"), None)
        rows.append({"id": cid, "name": c["name"], "profile": c["profile"], "early_warning": ew,
                     "legacy_flag": c["legacy_flag_date"], "default_date": c["default_date"]})
    defaulters = [r for r in rows if r["default_date"]]
    non_def = [r for r in rows if not r["default_date"]]
    caught = [r for r in defaulters if r["early_warning"] and r["early_warning"] <= r["default_date"]]
    leads_new = [_days(r["early_warning"], r["default_date"]) for r in caught]
    leads_old = [_days(r["legacy_flag"], r["default_date"]) for r in defaulters if r["legacy_flag"]]
    new_fp = [r for r in non_def if r["early_warning"]]
    old_fp = [r for r in non_def if r["legacy_flag"]]
    new_flags, old_flags = len(caught) + len(new_fp), len(defaulters) + len(old_fp)
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    return {
        "customers": len(rows), "defaulters": len(defaulters),
        "new_system": {"caught": len(caught), "false_positives": len(new_fp),
                       "precision": round(len(caught) / new_flags, 2) if new_flags else None,
                       "avg_lead_days_before_default": avg(leads_new)},
        "legacy_rule": {"caught": len([r for r in defaulters if r["legacy_flag"]]), "false_positives": len(old_fp),
                        "precision": round(len(defaulters) / old_flags, 2) if old_flags else None,
                        "avg_lead_days_before_default": avg(leads_old)},
        "extra_lead_days": round(avg(leads_new) - avg(leads_old), 1) if leads_new and leads_old else None,
        "rows": rows,
    }
