"""Builds an anonymised snapshot from the real bank data in TiDB (read-only) and writes data/snapshot_real.json.

Run (needs `pip install pymysql`; credentials come from the environment, never from a file in the repo):
  TIDB_HOST=... TIDB_USER=... TIDB_PASSWORD=... .venv/bin/python -m scripts.import_real
Serve it:  DATA_SOURCE=real uvicorn app.main:app      (Neo4j is not touched)

Source tables
  stg.monthly_hdv_duno   credit / deposit balances per CIF per month (2024-01 .. latest)
  stg.corporate_info     industry, segment, yearly internal credit rating (D1-D3 = bad)
  stg.daily_casa_txn_above_100_mil   large transactions, used to find related companies via the remark text

Mapping onto the existing snapshot schema. The proxies are real data used as stand-ins, NOT the original quantity:
  revenue          <- average deposit balance (VND million)        proxy for business turnover
  net_cashflow     <- month-over-month change of deposit balance    proxy for cash flow
  dpd              <- 0 (days past due is not in the data)          legacy DPD rule cannot be reproduced
  valuations       <- [] (no collateral data)                       collateral signal never fires
  related company  <- another corporate named in a customer's transaction remark; its "dpd" is 91 from the
                      1st of the year it was first rated D (proxy), else 0
  low_deposit_coverage (new, P-40)  <- average deposit / credit balance below 0.5% for 6 months; real data, counts as 2 signals
  industry growth  <- year-over-year change of the industry's total deposit balance (3-month trailing)
Labels for the backtest: a customer rated D in 2026 but not in 2025 is a defaulter with default_date = 2026-01-01
(the rating is yearly, so this is the conservative reading: we must flag before the year starts, using 2024-2025 data only).
Customers never rated D in 2024-2026 are the healthy controls. Chronic D customers (already D in 2025) are excluded.

Privacy: CIFs and names are replaced by salted hashes and neutral labels. Remark text and real names stay in memory only.
"""
import argparse
import hashlib
import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import pymysql

from app.scoring import evaluate
from scripts.seed import POLICIES

OUT = Path(__file__).resolve().parent.parent / "data" / "snapshot_real.json"
SALT = os.getenv("EWS_SALT", "ews-demo")
N_POS = int(os.getenv("REAL_POSITIVES", "100"))
N_NEG = int(os.getenv("REAL_CONTROLS", "200"))
DEFAULT_DATE = "2026-01-01"
D_DPD = 91

P40 = ("P-40", "Account Activity",
       "A borrower whose average deposit balance stays below 0.5% of its credit balance for 6 consecutive months has "
       "lost day-to-day account activity with the bank. This counts as two signals under P-17; review cash-flow "
       "visibility and the source of repayment.")

SEGMENTS = {"11": "Large Corporate", "12": "SME", "14": "FDI", "24": "Micro", "13": "Financial Institution"}

# First matching rule wins; text is the industry name without diacritics.
INDUSTRY_RULES = [
    ("Real Estate", ["BAT DONG SAN"]),
    ("Construction", ["XAY DUNG", "KIEN TRUC"]),
    ("Transport & Logistics", ["VAN TAI", "KHO BAI", "BUU CHINH"]),
    ("Energy & Utilities", ["DIEN, KHI DOT", "THOAT NUOC", "KHAI THAC, XU LY VA CUNG CAP NUOC", "DAU THO", "THAN ", "RAC THAI", "THAN COC"]),
    ("Metals & Mining", ["KIM LOAI", "KHAI KHOANG", "KHOANG", "KHAI THAC", "SAN XUAT DA "]),
    ("Food & Agriculture", ["NONG NGHIEP", "THUC PHAM", "THUY SAN", "LAM NGHIEP", "DO UONG", "THUOC LA"]),
    ("Textiles & Wood", ["DET", "TRANG PHUC", "GO VA", "GIAY", "GIUONG"]),
    ("Wholesale & Retail", ["BAN BUON", "BAN LE", "BAN, SUA CHUA"]),
    ("Manufacturing", ["SAN XUAT", "CHE TAO", "CONG NGHIEP", "CAO SU", "SUA CHUA, BAO DUONG", "IN, SAO CHEP"]),
]
OTHER = "Services"


def strip(s: str | None) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return re.sub(r"\s+", " ", "".join(ch for ch in s if unicodedata.category(ch) != "Mn")).upper().strip()


def industry_group(name: str | None) -> str:
    n = strip(name)
    for group, keys in INDUSTRY_RULES:
        if any(k in n for k in keys):
            return group
    return OTHER


def h(*parts: str, n: int = 8) -> str:
    return hashlib.sha256(("|".join((SALT, *parts))).encode()).hexdigest()[:n].upper()


def month_str(period_key: str) -> str:
    return period_key[:7] + "-01"


def connect():
    return pymysql.connect(host=os.environ["TIDB_HOST"], port=int(os.getenv("TIDB_PORT", "4000")),
                           user=os.environ["TIDB_USER"], password=os.environ["TIDB_PASSWORD"],
                           connect_timeout=10, read_timeout=300, cursorclass=pymysql.cursors.DictCursor)


def fetch(cur, sql, *args):
    cur.execute(sql, args)
    return cur.fetchall()


def load_population(cur):
    """Balances per (cif, month) for every borrower, plus industry/segment/rating per (cif, year)."""
    latest = fetch(cur, "SELECT MAX(period_key) m FROM stg.monthly_hdv_duno")[0]["m"]
    borrowers = {r["cif"] for r in fetch(cur, "SELECT DISTINCT cif FROM stg.monthly_hdv_duno "
                                              "WHERE period_key=%s AND credit_eop_converted>0", latest)}
    bal = defaultdict(dict)
    for r in fetch(cur, "SELECT cif, period_key, SUM(deposit_avg_converted) dep_avg, SUM(deposit_eop_converted) dep_eop, "
                        "SUM(credit_eop_converted) credit FROM stg.monthly_hdv_duno "
                        "WHERE cif IN (SELECT cif FROM stg.monthly_hdv_duno WHERE period_key=%s AND credit_eop_converted>0) "
                        "GROUP BY cif, period_key", latest):
        bal[r["cif"]][month_str(r["period_key"])] = r
    info = defaultdict(dict)  # cif -> year -> {industry, segment, rating}
    for r in fetch(cur, "SELECT cif, year, industry_name, segment_code, credit_rating FROM stg.corporate_info ORDER BY row_num"):
        slot = info[r["cif"]].setdefault(r["year"], {"industry": None, "segment": None, "rating": None})
        slot["industry"] = r["industry_name"] or slot["industry"]
        slot["segment"] = r["segment_code"] or slot["segment"]
        slot["rating"] = r["credit_rating"] or slot["rating"]
    return latest, borrowers, bal, info


def is_d(info, cif, year) -> bool | None:
    rating = info.get(cif, {}).get(year, {}).get("rating")
    return None if rating is None else rating.startswith("D")


def profile_of(info, cif) -> str:
    """defaulter = non-D in 2025 and D in 2026; healthy = non-D in both; already_npl = D in 2025 (no pre-default history to test on);
    unlabeled = no rating in 2025 or 2026. The last two are shown in the app but ignored by the backtest and calibration."""
    d25, d26 = is_d(info, cif, 2025), is_d(info, cif, 2026)
    if d25:
        return "already_npl"
    if d25 is False and d26 is not None:
        return "defaulter" if d26 else "healthy"
    return "unlabeled"


def pick(cur, borrowers, bal, info, months, n_pos, n_neg):
    """Defaulters = non-D in 2025 and D in 2026; controls = rated and never D. Prefer customers with large transactions."""
    full = {c for c in borrowers if all(m in bal[c] for m in months)}
    pos = [c for c in full if is_d(info, c, 2025) is False and is_d(info, c, 2026) is True]
    neg = [c for c in full if all(is_d(info, c, y) is False for y in (2024, 2025, 2026))]
    key = lambda c: h(c)  # random but reproducible; preferring customers with large transactions would bias the sample towards active accounts
    print(f"eligible: {len(pos)} defaulters, {len(neg)} controls (with 31 months of balances)")
    return sorted(pos, key=key)[:n_pos], sorted(neg, key=key)[:n_neg]


def industry_trends(bal, info, months):
    """YoY growth of each industry's total deposit balance, on 3-month trailing sums."""
    tot = defaultdict(lambda: defaultdict(float))
    for cif, by_month in bal.items():
        years = info.get(cif, {})
        ind = industry_group(years[max(years)]["industry"]) if years else OTHER
        for m, r in by_month.items():
            tot[ind][m] += float(r["dep_avg"] or 0)
    trends = {}
    for ind in {industry_group(None), *(g for g, _ in INDUSTRY_RULES), OTHER}:
        series = [tot[ind].get(m, 0.0) for m in months]
        roll = [sum(series[max(0, i - 2):i + 1]) for i in range(len(months))]
        trends[ind] = [{"month": m, "growth_pct": round((roll[i] / roll[i - 12] - 1) * 100, 1)
                       if i >= 14 and roll[i - 12] > 0 else 0.0} for i, m in enumerate(months)]
    return trends


def chunks(xs, n=500):
    xs = list(xs)
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def in_list(cur, sql, cifs, *args):
    """Runs `sql` (with one `{ph}` placeholder for the CIF list) in chunks and yields every row."""
    for chunk in chunks(cifs):
        yield from fetch(cur, sql.format(ph=",".join(["%s"] * len(chunk))), *args, *chunk)


# name, table, expression that yields the period of a row. Every source is optional except hdv and corporate_info.
SOURCES = {
    "monthly_hdv_duno": ("stg.monthly_hdv_duno", "period_key"),
    "corporate_info": ("stg.corporate_info", "CAST(year AS CHAR)"),
    "daily_casa_txn_above_100_mil": ("stg.daily_casa_txn_above_100_mil", "period_key"),
    "monthly_khdn_spdv": ("stg.monthly_khdn_spdv", "period_key"),
    "monthly_casa": ("stg.monthly_casa", "period_key"),
}


def inventory(cur) -> dict:
    """What has been loaded so far. Everything downstream adapts to this, so new files only need to be loaded, not coded."""
    out = {}
    for name, (table, expr) in SOURCES.items():
        rows = fetch(cur, f"SELECT {expr} m, COUNT(*) n FROM {table} GROUP BY 1 ORDER BY 1")
        periods = sorted({r["m"][:10] for r in rows if r["m"]})
        out[name] = {"rows": sum(r["n"] for r in rows), "periods": len(periods),
                     "first": periods[0] if periods else None, "last": periods[-1] if periods else None}
    return out


def month_features(cur, cifs) -> dict:
    """Optional per-month fields from the sources that have data: cif -> month -> {field: value}.
    A field is only present for months where its source has data, so downstream code can tell 'zero' from 'unknown'."""
    feats = defaultdict(lambda: defaultdict(dict))
    for r in in_list(cur, "SELECT cif, period_key, COUNT(DISTINCT CASE WHEN net_income_ftp<>0 THEN product_l3_raw END) n_products, "
                          "SUM(net_income_ftp) income FROM stg.monthly_khdn_spdv WHERE cif IN ({ph}) GROUP BY cif, period_key", cifs):
        feats[r["cif"]][month_str(r["period_key"])].update(n_products=int(r["n_products"]), product_income=round(float(r["income"] or 0) / 1e6, 2))
    for r in in_list(cur, "SELECT cif, SUBSTR(period_key,1,7) m, SUM(eod_balance_converted) eod, COUNT(*) n FROM stg.monthly_casa "
                          "WHERE cif IN ({ph}) GROUP BY cif, SUBSTR(period_key,1,7)", cifs):
        feats[r["cif"]][r["m"] + "-01"].update(casa_eod=round(float(r["eod"] or 0) / 1e6, 1), n_accounts=int(r["n"]))
    for r in in_list(cur, "SELECT cif, DATE_FORMAT(calendar_date,'%%Y-%%m') m, SUM(CASE WHEN dc_indicator='C' THEN amount_converted END) cin, "
                          "SUM(CASE WHEN dc_indicator='D' THEN amount_converted END) cout, COUNT(*) n, COUNT(DISTINCT calendar_date) days "
                          "FROM stg.daily_casa_txn_above_100_mil WHERE cif IN ({ph}) GROUP BY cif, DATE_FORMAT(calendar_date,'%%Y-%%m')", cifs):
        feats[r["cif"]][r["m"] + "-01"].update(txn_in=round(float(r["cin"] or 0) / 1e6, 1), txn_out=round(float(r["cout"] or 0) / 1e6, 1),
                                               txn_count=int(r["n"]), txn_days=int(r["days"]))  # txn_days = how many days of the month are loaded
    return feats


def counterparty_edges(cur, chosen) -> dict:
    """Counterparties named in a chosen customer's transaction remarks (exact normalised company name, or a known account number).
    Returns cif -> other cif -> {amount, n, first, last}; amount is VND million, first/last are ISO dates."""
    names = {}
    for r in fetch(cur, "SELECT cif, customer_name FROM stg.corporate_info WHERE year=2026"):
        n = strip(r["customer_name"])
        if len(n) >= 14 and n.count(" ") >= 2:
            names.setdefault(n, r["cif"])
    accounts = {r["account_no"]: r["cif"] for r in fetch(cur, "SELECT DISTINCT account_no, cif FROM stg.daily_casa_txn_above_100_mil WHERE account_no IS NOT NULL")}
    prefix = {" ".join(n.split(" ")[:2]) for n in names}
    max_words = max((len(n.split(" ")) for n in names), default=0)
    out = defaultdict(dict)
    for r in in_list(cur, "SELECT cif, calendar_date, txn_remark, amount_converted FROM stg.daily_casa_txn_above_100_mil WHERE cif IN ({ph})", chosen):
        rem = strip(r["txn_remark"])
        words, found = rem.split(" "), set()
        for i in range(len(words) - 2):
            if " ".join(words[i:i + 2]) in prefix:
                for j in range(i + 3, min(len(words), i + max_words) + 1):
                    if (o := names.get(" ".join(words[i:j]))):
                        found.add(o)
        found.update(accounts[num] for num in re.findall(r"\b\d{9,12}\b", rem) if num in accounts)
        day = r["calendar_date"].isoformat()
        for other in found - {r["cif"]}:
            e = out[r["cif"]].setdefault(other, {"amount": 0.0, "n": 0, "first": day, "last": day})
            e["amount"] += float(r["amount_converted"] or 0) / 1e6
            e["n"] += 1
            e["first"], e["last"] = min(e["first"], day), max(e["last"], day)
    return out


def company_dpd(info, cif, months):
    first_d = min((y for y, v in info.get(cif, {}).items() if (v["rating"] or "").startswith("D")), default=None)
    return [{"month": m, "dpd": D_DPD if first_d and int(m[:4]) >= first_d else 0} for m in months]


def build_customer(cif, label, bal, info, months, feats, edges, first_month, edge_since, to_cid):
    years = info[cif]
    last_year = years[max(years)]
    ind = industry_group(last_year["industry"])
    cid = f"R{h(cif)}"
    snaps, prev = [], None
    for m in months:
        if m not in bal[cif]:
            continue  # months the customer has no balance row for are simply absent, not zero
        b = bal[cif][m]
        eop = float(b["dep_eop"] or 0) / 1e6
        snaps.append({"month": m, "revenue": round(max(float(b["dep_avg"] or 0) / 1e6, 1.0), 1),
                      "net_cashflow": round(eop - prev, 1) if prev is not None else 0.0, "dpd": 0,
                      "deposit": round(float(b["dep_avg"] or 0) / 1e6, 1), "credit": round(float(b["credit"] or 0) / 1e6, 1),
                      **feats.get(cif, {}).get(m, {})})
        prev = eop
    loan = float(bal[cif][months[-1]]["credit"]) / 1e6
    return {
        "id": cid, "name": f"Customer {cid[1:5]}", "profile": label, "is_scenario": False,
        "default_date": DEFAULT_DATE if label == "defaulter" else None, "legacy_flag_date": None,
        "industry": ind, "segment": SEGMENTS.get(last_year["segment"], "SME"),
        "loan_id": f"L{h(cif, 'loan')}", "loan_amount": round(loan), "loan_number": int(h(cif, "n", n=5), 16) % 90000 + 10000,
        "collateral_id": f"K{h(cif, 'col')}", "collateral_kind": "Not available",
        "snapshots": snaps, "valuations": [], "guarantors": [], "events": [],
        "related": [{"id": f"CP{h(o)}", "name": f"Company {h(o)[:4]}", "weight": round(e["amount"], 1), "n_txn": e["n"],
                     "since": max(first_month, e["first"][:7] + "-01") if edge_since == "first_seen" else first_month,
                     "snapshots": company_dpd(info, o, months)}
                    for o, e in top_edges(edges.get(cif, {}), to_cid)],
    }


def top_edges(edges: dict, to_cid: dict, limit: int = 6):
    """Strongest counterparties that are not themselves in the sample (those become customer-to-customer edges instead)."""
    return sorted(((o, e) for o, e in edges.items() if o not in to_cid), key=lambda kv: -kv[1]["amount"])[:limit]


def customer_edges(edges: dict, to_cid: dict, first_month: str, edge_since: str) -> list:
    """Customer-to-customer transaction edges inside the sample, one per unordered pair."""
    merged = {}
    for a, nbrs in edges.items():
        for b, e in nbrs.items():
            if a in to_cid and b in to_cid:
                key = tuple(sorted((to_cid[a], to_cid[b])))
                m = merged.setdefault(key, {"weight": 0.0, "n_txn": 0, "first": e["first"]})
                m["weight"] += e["amount"]; m["n_txn"] += e["n"]; m["first"] = min(m["first"], e["first"])
    return [{"source": a, "target": b, "type": "transaction", "weight": round(m["weight"], 1), "n_txn": m["n_txn"],
             "since": max(first_month, m["first"][:7] + "-01") if edge_since == "first_seen" else first_month}
            for (a, b), m in sorted(merged.items())]


def pick_scenario(customers, trends):
    """The demo scenario (id C_A): a defaulter with related companies and the most signals just before the default date."""
    best, best_key = None, (-1, -1, -1)
    for c in customers.values():
        if c["profile"] != "defaulter":
            continue
        r = evaluate(c, trends, "2025-12-01")
        key = (bool(c["related"]), r["signal_count"], len(c["related"]))
        if key > best_key:
            best, best_key = c["id"], key
    old = customers.pop(best)
    old.update(id="C_A", name="Customer A", is_scenario=True)
    return {"C_A": old, **customers}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inventory", action="store_true", help="only print what is loaded in TiDB, then exit")
    ap.add_argument("--positives", type=int, help="sample this many defaulters (default: take every borrower)")
    ap.add_argument("--controls", type=int, help="sample this many healthy customers")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--edge-since", choices=["first_seen", "window_start"], default="first_seen",
                    help="first_seen: an edge exists from the month its first transaction was seen (no look-ahead, the honest default). "
                         "window_start: assume it always existed (useful for demos while only recent transactions are loaded)")
    args = ap.parse_args()
    with connect() as conn, conn.cursor() as cur:
        inv = inventory(cur)
        for name, v in inv.items():
            print(f"  {name:32s} {v['rows']:>9,} rows  {v['periods']:>3} periods  {v['first']} .. {v['last']}")
        if args.inventory:
            return
        latest, borrowers, bal, info = load_population(cur)
        months = sorted({m for c in borrowers for m in bal[c]})
        print(f"{len(borrowers)} borrowers, {len(months)} months ({months[0]} .. {months[-1]})")
        if args.positives is None and args.controls is None:
            groups: dict = defaultdict(list)
            for c in sorted(borrowers):
                groups[profile_of(info, c)].append(c)
            print("all borrowers by profile:", {k: len(v) for k, v in sorted(groups.items())})
        else:
            pos, neg = pick(cur, borrowers, bal, info, months, args.positives or N_POS, args.controls or N_NEG)
            groups = {"defaulter": pos, "healthy": neg}
        chosen = [c for g in groups.values() for c in g]
        feats = month_features(cur, chosen)
        edges = counterparty_edges(cur, chosen)
        trends = industry_trends(bal, info, months)
    to_cid = {c: f"R{h(c)}" for c in chosen}
    customers = {}
    for label, group in groups.items():
        for cif in group:
            c = build_customer(cif, label, bal, info, months, feats, edges, months[0], args.edge_since, to_cid)
            customers[c["id"]] = c
    customers = pick_scenario(customers, trends)
    if "C_A" in customers:  # the scenario customer was renamed; keep edges pointing at it
        old = next(cid for cid in to_cid.values() if cid not in customers)
        to_cid = {k: ("C_A" if v == old else v) for k, v in to_cid.items()}
    data = {
        "customers": customers, "industry_trends": trends,
        "edges": customer_edges(edges, to_cid, months[0], args.edge_since),
        "policies": {pid: {"id": pid, "title": t, "text": x} for pid, t, x in POLICIES + [P40]},
        "meta": {"source": "real", "latest_month": months[-1], "months": [months[0], months[-1]],
                 "industries": sorted(trends), "segments": sorted({c["segment"] for c in customers.values()}),
                 "default_customer": "C_A", "default_date_label": DEFAULT_DATE, "edge_since": args.edge_since,
                 "inventory": inv,
                 "extra_fields": sorted({k for c in customers.values() for s in c["snapshots"] for k in s} - {"month", "revenue", "net_cashflow", "dpd", "deposit", "credit"}),
                 "proxies": {"revenue": "average deposit balance", "net_cashflow": "month-over-month change of deposit balance",
                             "dpd": "not available (0)", "collateral": "not available",
                             "related_overdue": "related company rated D (internal rating)",
                             "labels": "defaulter = rated D in 2026 but not in 2025"}},
    }
    args.out.parent.mkdir(exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    n_rel = sum(1 for c in customers.values() if c["related"])
    print(f"wrote {args.out.name} ({args.out.stat().st_size / 1e6:.0f} MB): {({k: len(v) for k, v in groups.items()})}, {n_rel} customers with related companies, "
          f"{len(data['edges'])} customer-to-customer edges, extra monthly fields: {data['meta']['extra_fields'] or 'none yet'}")


if __name__ == "__main__":
    main()
