"""Does risk flowing in from neighbours predict who deteriorates later? Re-run it whenever new data is loaded.

Run:  .venv/bin/python -m scripts.calibrate_contagion [--snapshot data/snapshot_real.json] [--as-of 2025-10-01]

Population at `as_of`: customers that have not defaulted yet. Positive = defaults after as_of, negative = never defaults.
The graph and every neighbour's state are taken as of `as_of` only (relations count from their `since` month), so there is no look-ahead.
For each alpha/hops it reports AUC and top-decile lift of: own rule score, contagion inflow alone, and the two added together.
`inflow | own<0.3` is the AUC of inflow among customers whose own score is still low, i.e. what contagion adds that the rules cannot see.
"""
import argparse
import json
from pathlib import Path

from app.contagion import propagate
from app.scoring import month_floor

DATA = Path(__file__).resolve().parent.parent / "data"


def auc(scores, labels):
    """Probability that a random positive scores above a random negative (ties count half)."""
    pairs = sorted(zip(scores, labels))
    pos = sum(labels)
    neg = len(labels) - pos
    if not pos or not neg:
        return float("nan")
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        rank_sum += (i + 1 + j) / 2 * sum(1 for _, y in pairs[i:j] if y)
        i = j
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


def top_lift(scores, labels, frac=0.10):
    k = max(1, int(len(labels) * frac))
    order = sorted(range(len(labels)), key=lambda i: -scores[i])[:k]
    base = sum(labels) / len(labels)
    return (sum(labels[i] for i in order) / k) / base if base else float("nan")


def default_as_of(customers):
    dates = sorted(c["default_date"] for c in customers.values() if c["default_date"])
    y, m = int(dates[len(dates) // 2][:4]), int(dates[len(dates) // 2][5:7]) - 3
    y, m = (y - 1, m + 12) if m < 1 else (y, m)
    return f"{y}-{m:02d}-01"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, default=DATA / "snapshot.json")
    ap.add_argument("--as-of")
    args = ap.parse_args()
    history = json.loads(args.snapshot.read_text())
    cs = history["customers"]
    as_of = month_floor(args.as_of or default_as_of(cs))
    pop = [cid for cid, c in cs.items() if c["profile"] not in ("unlabeled", "already_npl") and (not c["default_date"] or c["default_date"] > as_of)]
    y = [1 if cs[cid]["default_date"] else 0 for cid in pop]
    print(f"{args.snapshot.name}  as_of {as_of}  population {len(pop)}  positives {sum(y)}  base rate {sum(y) / len(y):.3f}")
    edges = sum(1 for c in cs.values() for r in c["related"] if r["since"] <= as_of) + sum(1 for e in history.get("edges", []) if e["since"] <= as_of)
    print(f"relations visible at as_of: {edges}")
    print(f"{'alpha':>5} {'hops':>4} | {'AUC own':>7} {'AUC inflow':>10} {'AUC sum':>7} | {'lift own':>8} {'lift inflow':>11} {'lift sum':>8} | {'inflow|own<0.3':>14} | exposed pos/neg")
    for alpha in (0.25, 0.5, 0.75, 1.0):
        for hops in (1, 2, 3):
            r = propagate(history, as_of, alpha, hops)
            own = [r[c]["own"] for c in pop]
            inflow = [r[c]["inflow"] for c in pop]
            both = [o + i for o, i in zip(own, inflow)]
            low = [k for k, o in enumerate(own) if o < 0.3]
            a_low = auc([inflow[k] for k in low], [y[k] for k in low])
            exp_p = sum(1 for k in range(len(pop)) if inflow[k] > 0 and y[k])
            exp_n = sum(1 for k in range(len(pop)) if inflow[k] > 0 and not y[k])
            print(f"{alpha:5.2f} {hops:4d} | {auc(own, y):7.3f} {auc(inflow, y):10.3f} {auc(both, y):7.3f} | "
                  f"{top_lift(own, y):8.2f} {top_lift(inflow, y):11.2f} {top_lift(both, y):8.2f} | {a_low:14.3f} | {exp_p}/{exp_n}")
    if sum(y) < 20:
        print("WARNING: fewer than 20 positives, results are noise")


if __name__ == "__main__":
    main()
