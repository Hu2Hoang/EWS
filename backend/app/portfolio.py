"""Portfolio-level views for the Command Center: risk mix, industry heatmap and the monthly risk trend."""
from app.scoring import evaluate, month_floor, months_of

_series_cache: dict[int, list[dict]] = {}


def portfolio_series(history: dict) -> list[dict]:
    """Per month: customers by level, High-risk exposure, and how many loans each system flags (cached per dataset)."""
    key = id(history)
    if key in _series_cache:
        return _series_cache[key]
    custs, trends = history["customers"], history["industry_trends"]
    months = months_of(next(iter(custs.values())))
    out = []
    for m in months:
        counts = {"Low": 0, "Medium": 0, "High": 0}
        exposure, legacy = 0.0, 0
        for c in custs.values():
            r = evaluate(c, trends, m)
            counts[r["level"]] += 1
            if r["level"] == "High":
                exposure += c["loan_amount"]
            legacy += r["legacy_flag"]
        out.append({"month": m, **counts, "exposure_high": round(exposure), "legacy_flagged": legacy})
    _series_cache.clear()
    _series_cache[key] = out
    return out


def _prev_month(history: dict, as_of: str) -> str | None:
    months = months_of(next(iter(history["customers"].values())))
    earlier = [m for m in months if m < as_of]
    return earlier[-1] if earlier else None


def summary(history: dict, as_of: str) -> dict:
    as_of = month_floor(as_of)
    custs, trends = history["customers"], history["industry_trends"]
    prev = _prev_month(history, as_of)
    heat: dict[tuple, int] = {}
    counts = {"Low": 0, "Medium": 0, "High": 0}
    exposure = {"Low": 0.0, "Medium": 0.0, "High": 0.0}
    new_high = []
    for cid, c in custs.items():
        r = evaluate(c, trends, as_of)
        counts[r["level"]] += 1
        exposure[r["level"]] += c["loan_amount"]
        heat[(c["industry"], r["level"])] = heat.get((c["industry"], r["level"]), 0) + 1
        if r["level"] == "High" and prev and evaluate(c, trends, prev)["level"] != "High":
            new_high.append({"id": cid, "name": c["name"], "industry": c["industry"], "score": r["score"],
                             "loan_amount": c["loan_amount"], "signals": [s["code"] for s in r["signals"]]})
    industries = sorted({c["industry"] for c in custs.values()})
    return {
        "as_of": as_of, "customers": len(custs), "counts": counts,
        "exposure": {k: round(v) for k, v in exposure.items()},
        "heatmap": [{"industry": i, **{lvl: heat.get((i, lvl), 0) for lvl in ("Low", "Medium", "High")}}
                    for i in industries],
        "new_high": sorted(new_high, key=lambda r: -r["loan_amount"])[:10],
        "new_high_count": len(new_high),
        "series": [s for s in portfolio_series(history) if s["month"] <= as_of],
    }
