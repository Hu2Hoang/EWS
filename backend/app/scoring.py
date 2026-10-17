"""Early-warning signals and risk score (rule-based). The LLM takes no part in scoring.

Each signal carries: a policy reference, evidence (graph node ids) and the supporting figures.
Risk level follows policy P-17: 3+ signals = High, 2 = Medium, otherwise Low.
"""

THRESHOLDS = {
    "revenue_drop": 0.15,       # last-3-month revenue vs. baseline period
    "collateral_drop": 0.09,    # collateral value vs. peak of the last 6 valuations
    "dpd": 10,                  # P-08
    "industry_growth": -3.0,    # industry growth (%), two consecutive months
    "deposit_coverage": 0.005,  # P-40: average deposit / credit balance, 6 consecutive months (needs deposit + credit in snapshots)
}
WEIGHTS = {"revenue_drop": 20, "negative_cashflow": 20, "related_overdue": 15,
           "collateral_drop": 15, "dpd": 20, "industry_downturn": 10, "low_deposit_coverage": 25}
POINTS = {"low_deposit_coverage": 2}  # P-40: counts as two signals towards the P-17 level; every other signal counts as one


def month_floor(as_of: str) -> str:
    return as_of[:7] + "-01"


def active(rel: dict, as_of: str) -> bool:
    """A relation counts until it is ring-fenced (`until`, set by a completed mitigation task)."""
    return as_of < rel.get("until", "9999-12-31")


def _avg(xs):
    return sum(xs) / len(xs)


def evaluate(cust: dict, trends: dict, as_of: str) -> dict:
    as_of = month_floor(as_of)
    snaps = [s for s in cust["snapshots"] if s["month"] <= as_of]
    signals = []

    def add(code, label, detail, evidence, policy, weight=None):
        signals.append({"code": code, "label": label, "detail": detail, "evidence": evidence,
                        "policy": policy, "weight": weight or WEIGHTS[code]})

    n = len(snaps)
    if n >= 6:
        # Baseline: the 3 months ending 6 months ago (or the first 3 months on a short history)
        base_slice = snaps[n - 9:n - 6] if n >= 9 else snaps[:3]
        base, last = _avg([s["revenue"] for s in base_slice]), _avg([s["revenue"] for s in snaps[-3:]])
        drop = 1 - last / base
        if drop >= THRESHOLDS["revenue_drop"]:
            add("revenue_drop", f"90-day revenue down {drop:.0%}",
                {"drop_pct": round(drop * 100, 1), "recent_avg": round(last), "baseline_avg": round(base)},
                [cust["id"]], "P-17")

    if n >= 2 and all(s["net_cashflow"] < 0 for s in snaps[-2:]):
        add("negative_cashflow", "Negative net cash flow for 2 consecutive periods",
            {"last_two": [s["net_cashflow"] for s in snaps[-2:]]}, [cust["id"]], "P-17")

    if n >= 6 and all("deposit" in s and "credit" in s for s in snaps[-6:]):
        cover = [s["deposit"] / max(s["credit"], 1) for s in snaps[-6:]]
        if all(c < THRESHOLDS["deposit_coverage"] for c in cover):
            add("low_deposit_coverage", "Deposit balance below 0.5% of credit for 6 months",
                {"coverage_pct": [round(c * 100, 2) for c in cover], "credit": round(snaps[-1]["credit"])},
                [cust["id"]], "P-40")

    overdue = []
    for rel in cust["related"]:
        if not active(rel, as_of):
            continue
        latest = [s for s in rel["snapshots"] if s["month"] <= as_of]
        if latest and latest[-1]["dpd"] > 0:
            overdue.append({"company": rel["id"], "name": rel["name"], "dpd": latest[-1]["dpd"]})
    if overdue:
        add("related_overdue", "Related company is past due",
            {"companies": overdue}, [o["company"] for o in overdue], "P-31")

    vals = [v for v in cust["valuations"] if v["date"] <= as_of][-6:]
    if len(vals) >= 2:
        peak = max(v["value"] for v in vals)
        drop = 1 - vals[-1]["value"] / peak
        if drop >= THRESHOLDS["collateral_drop"]:
            add("collateral_drop", f"Collateral value down {drop:.0%}",
                {"drop_pct": round(drop * 100, 1), "latest": round(vals[-1]["value"]), "peak": round(peak)},
                [cust["collateral_id"]], "P-22")

    if snaps and snaps[-1]["dpd"] >= THRESHOLDS["dpd"]:
        dpd = snaps[-1]["dpd"]
        add("dpd", f"Days past due up to {dpd}", {"dpd": dpd}, [cust["loan_id"]], "P-08",
            weight=WEIGHTS["dpd"] + (10 if dpd >= 30 else 0))

    tr = [t for t in trends[cust["industry"]] if t["month"] <= as_of][-2:]
    if len(tr) == 2 and all(t["growth_pct"] <= THRESHOLDS["industry_growth"] for t in tr):
        add("industry_downturn", f"{cust['industry']} industry in downturn",
            {"growth_pct": [t["growth_pct"] for t in tr]}, [cust["industry"]], "P-17")

    count = len(signals)
    points = sum(POINTS.get(s["code"], 1) for s in signals)
    return {
        "as_of": as_of,
        "signals": signals,
        "signal_count": count,
        "points": points,
        "score": min(100, sum(s["weight"] for s in signals)),
        "level": "High" if points >= 3 else "Medium" if points == 2 else "Low",
        "legacy_flag": bool(snaps and any(s["dpd"] >= 30 for s in snaps)),
    }


def months_of(cust: dict) -> list[str]:
    return [s["month"] for s in cust["snapshots"]]
