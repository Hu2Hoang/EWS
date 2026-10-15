"""What-if / stress testing: apply a hypothetical shock to a copy of the portfolio and re-run the rules.

Nothing is written to the database. The rules engine is the same one used for live scoring, so a flipped
customer is explained by the same signals. Shock mechanics (deliberately simple and documented):
  - industry shock X%: revenue of the industry's customers falls X% over the last 3 months, net cash flow of the last
    2 months falls by half that share of revenue, and industry growth is set to -max(4, X/3)% for the last 2 months;
  - failed company: the company is 45 days past due from the as-of month onward (contagion via RELATED_TO);
  - collateral shock X%: the latest valuation of matching collateral falls X% (optionally only one collateral kind).
"""
from app.scoring import evaluate, month_floor

LEVEL_RANK = {"Low": 0, "Medium": 1, "High": 2}


def _shock_customer(c: dict, as_of: str, industry_pct: float, failed: set, col_pct: float, col_kind: str | None) -> dict:
    c2 = dict(c)
    if industry_pct:
        snaps = [dict(s) for s in c["snapshots"]]
        idx = [i for i, s in enumerate(snaps) if s["month"] <= as_of]
        for i in idx[-3:]:
            snaps[i]["revenue"] = round(snaps[i]["revenue"] * (1 - industry_pct / 100), 1)
        for i in idx[-2:]:
            snaps[i]["net_cashflow"] = round(snaps[i]["net_cashflow"] - snaps[i]["revenue"] * industry_pct / 200, 1)
        c2["snapshots"] = snaps
    if failed:
        rel = []
        for r in c["related"]:
            if r["id"] in failed:
                r = {**r, "snapshots": [{**s, "dpd": max(s["dpd"], 45) if s["month"] >= as_of else s["dpd"]}
                                        for s in r["snapshots"]]}
            rel.append(r)
        c2["related"] = rel
    if col_pct and (col_kind in (None, "", "All") or c["collateral_kind"] == col_kind):
        vals = [dict(v) for v in c["valuations"]]
        idx = [i for i, v in enumerate(vals) if v["date"] <= as_of]
        if idx:
            vals[idx[-1]]["value"] = round(vals[idx[-1]]["value"] * (1 - col_pct / 100), 1)
        c2["valuations"] = vals
    return c2


def simulate(history: dict, as_of: str, industry_shocks: dict[str, float], failed_companies: list[str],
             collateral_drop: float = 0.0, collateral_kind: str | None = None) -> dict:
    as_of = month_floor(as_of)
    trends = {k: list(v) for k, v in history["industry_trends"].items()}
    for ind, pct in industry_shocks.items():
        if pct and ind in trends:
            g = -max(4.0, pct / 3)
            tr = trends[ind]
            idx = [i for i, t in enumerate(tr) if t["month"] <= as_of]
            for i in idx[-2:]:
                tr[i] = {**tr[i], "growth_pct": g}
    failed = set(failed_companies)

    before_counts, after_counts = {"Low": 0, "Medium": 0, "High": 0}, {"Low": 0, "Medium": 0, "High": 0}
    exp_before = exp_after = 0.0
    flipped = []
    by_industry: dict[str, dict] = {}
    for cid, c in history["customers"].items():
        b = evaluate(c, history["industry_trends"], as_of)
        c2 = _shock_customer(c, as_of, industry_shocks.get(c["industry"], 0.0), failed, collateral_drop, collateral_kind)
        a = evaluate(c2, trends, as_of)
        before_counts[b["level"]] += 1
        after_counts[a["level"]] += 1
        if b["level"] == "High":
            exp_before += c["loan_amount"]
        if a["level"] == "High":
            exp_after += c["loan_amount"]
        row = by_industry.setdefault(c["industry"], {"industry": c["industry"], "before_high": 0, "after_high": 0})
        row["before_high"] += b["level"] == "High"
        row["after_high"] += a["level"] == "High"
        if LEVEL_RANK[a["level"]] > LEVEL_RANK[b["level"]]:
            new_codes = sorted({s["code"] for s in a["signals"]} - {s["code"] for s in b["signals"]})
            via = [r["id"] for r in c["related"] if r["id"] in failed]
            flipped.append({"id": cid, "name": c["name"], "industry": c["industry"], "loan_amount": c["loan_amount"],
                            "before": b["level"], "after": a["level"], "score_before": b["score"],
                            "score_after": a["score"], "new_signals": new_codes, "via_companies": via})
    flipped.sort(key=lambda r: (-LEVEL_RANK[r["after"]], -r["loan_amount"]))
    return {
        "as_of": as_of,
        "scenario": {"industry_shocks": industry_shocks, "failed_companies": sorted(failed),
                     "collateral_drop": collateral_drop, "collateral_kind": collateral_kind},
        "before": before_counts, "after": after_counts,
        "exposure_high_before": round(exp_before), "exposure_high_after": round(exp_after),
        "flipped": flipped,
        "flipped_count": len(flipped),
        "by_industry": sorted(by_industry.values(), key=lambda r: -(r["after_high"] - r["before_high"])),
    }
