"""Generates synthetic data and loads it into Neo4j Aura.

Run:  .venv/bin/python -m scripts.seed
WARNING: deletes ALL existing data in the database (including recorded decisions).

Graph model
  (Customer)-[:BORROWS]->(Loan)-[:SECURED_BY]->(Collateral)-[:HAS_VALUATION]->(Valuation)
  (Customer)-[:OPERATES_IN]->(Industry)-[:HAS_TREND]->(IndustryTrend)
  (Customer)-[:IN_SEGMENT]->(Segment)
  (Customer)-[:RELATED_TO {since}]->(Company)
  (Guarantor)-[:GUARANTEES]->(Loan)
  (Customer|Company)-[:HAS_SNAPSHOT]->(Snapshot {month, revenue, net_cashflow, dpd})
  (RiskEvent {date})-[:AFFECTS]->(Customer|Company|Collateral|Industry)
  (Policy {id, text})

Ground-truth labels live on Customer: default_date, profile, legacy_flag_date.
Legacy rule: flag when DPD >= 30.
"""
import os
import random
from datetime import date

from app.db import run

random.seed(42)

N_CUSTOMERS = int(os.getenv("SEED_CUSTOMERS", "1200"))  # Aura Free allows 200k nodes; ~40 nodes per customer
N_MONTHS = 18  # 2025-04 .. 2026-09
START = (2025, 4)
LEGACY_DPD = 30


def month_str(i: int) -> str:
    y, m = START
    m += i
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1).isoformat()


MONTHS = [month_str(i) for i in range(N_MONTHS)]

INDUSTRIES = ["Seafood", "Real Estate", "Textiles", "Logistics", "Agriculture", "Retail"]
SEGMENTS = ["SME", "Mid-Market", "Large Corporate"]

POLICIES = [
    ("P-17", "Early Warning",
     "A loan with 3 or more early-warning signals within 90 days (revenue down >=15%, "
     "net cash flow negative for 2 consecutive periods, related company past due, collateral down >=10%, "
     "rising DPD) must be moved to the High Risk group and monitored monthly."),
    ("P-22", "Collateral",
     "When collateral value falls 10% or more from the latest valuation, the credit officer must request "
     "additional collateral or reduce the credit limit within 30 days."),
    ("P-31", "Related Parties",
     "If a related company or guarantor becomes past due (from 1 day), review contagion risk and "
     "reassess the customer's repayment capacity."),
    ("P-08", "Disbursement Hold",
     "A High Risk loan with DPD of 10 days or more and negative cash flow has further disbursements "
     "suspended until approved by the competent authority."),
]


def clamp(x, lo=0):
    return max(lo, x)


# Fully synthetic names (no real entities): <place word> + <industry noun> + <legal form>
PLACES = ["Bien Dong", "Song Hong", "Thang Long", "Hai Phong", "Da Nang", "Sai Gon", "Mekong", "An Phat", "Viet Tin",
          "Hoa Binh", "Phuong Nam", "Tan Thanh", "Hung Thinh", "Kim Long", "Thanh Cong", "Dai Viet", "Minh Phu",
          "Nam Viet", "Hoang Gia", "Bao Long", "Phu My", "Truong An", "Tay Do", "Cuu Long", "Dong A", "Quang Minh"]
NOUNS = {"Seafood": ["Seafood", "Aqua", "Fisheries"], "Real Estate": ["Land", "Homes", "Property"],
         "Textiles": ["Garment", "Textile", "Fabrics"], "Logistics": ["Logistics", "Transport", "Freight"],
         "Agriculture": ["Agri", "Farms", "Coffee"], "Retail": ["Mart", "Retail", "Trading"]}
FORMS = ["Co.", "JSC", "Ltd."]


def company_name(industry: str, used: set) -> str:
    for attempt in range(40):
        name = f"{random.choice(PLACES)} {random.choice(NOUNS[industry])} {random.choice(FORMS)}"
        if attempt >= 20:  # the combinations run out on large portfolios; add a branch number to stay unique
            name += f" {random.randint(2, 99)}"
        if name not in used:
            used.add(name)
            return name
    raise RuntimeError("could not generate a unique company name")


def gen_customer_series(profile: str, onset: int, base_rev: float):
    """Returns (snapshots, default_idx). Revenue and cash flow are in VND millions."""
    snaps, rev = [], base_rev
    default_idx = None
    for i in range(N_MONTHS):
        rev_noise = rev * random.uniform(0.96, 1.04)
        cf = base_rev * random.uniform(0.04, 0.12)
        dpd = 0
        if profile == "defaulter":
            k = i - onset
            if k >= 1:
                rev *= 0.92
                rev_noise = rev * random.uniform(0.98, 1.02)
            if k in (3, 4):
                cf = -base_rev * random.uniform(0.04, 0.08)
            elif k > 4:
                cf = -base_rev * random.uniform(0.05, 0.12)
            dpd = {5: 12, 6: 35, 7: 60, 8: 90}.get(k, 90 if k > 8 else 0)
            if k == 8:
                default_idx = i
        elif profile == "false_positive":
            # Legacy rule false alarm: a technical delay of 1-2 periods, then recovery; fundamentals stay healthy
            if i == onset:
                dpd = random.choice([32, 35, 40])
            elif i == onset + 1:
                dpd = random.choice([5, 8])
        elif profile == "stressed":
            # Genuine but temporary stress: revenue dips ~20% for 3 months and thin cash flow, then recovers. Never defaults.
            if onset <= i < onset + 3:
                rev_noise = rev * random.uniform(0.76, 0.84)
                cf = base_rev * random.uniform(-0.03, 0.02)
        else:  # healthy, with an occasional thin cash-flow period
            if random.random() < 0.06:
                cf = base_rev * random.uniform(-0.02, 0.01)
        snaps.append({"month": MONTHS[i], "revenue": round(rev_noise, 1),
                      "net_cashflow": round(cf, 1), "dpd": dpd})
    return snaps, default_idx


def company_series(overdue_from: int | None):
    out = []
    for i in range(N_MONTHS):
        dpd = 0
        if overdue_from is not None and i >= overdue_from:
            dpd = min(5 + 12 * (i - overdue_from), 90)
        out.append({"month": MONTHS[i], "revenue": 0, "net_cashflow": 0, "dpd": dpd})
    return out


def build():
    customers, loans, collaterals, valuations = [], [], [], []
    companies, guarantors = [], []
    cust_snaps, comp_snaps = [], []
    related, guarantees, events = [], [], []

    # Related companies are shared across customers, which gives the graph real structure
    n_comp = max(25, N_CUSTOMERS // 8)
    comp_pool = [f"CP{n:03d}" for n in range(1, n_comp + 1)]
    used_names: set = set()
    for cid in comp_pool:
        companies.append({"id": cid, "name": company_name(random.choice(INDUSTRIES), used_names)})
    comp_name = {c["id"]: c["name"] for c in companies}
    comp_overdue = {}  # company_id -> month the company becomes past due
    shared_with_b = 0
    n_guar = max(7, N_CUSTOMERS // 20)
    for n in range(1, n_guar + 1):
        guarantors.append({"id": f"G{n:03d}", "name": f"Guarantor {n}"})

    # --- Customer A: the demo scenario ---
    profiles = [("A", "defaulter", 9, "Seafood")]
    n_def, n_fp, n_stress = round(N_CUSTOMERS * 0.06), round(N_CUSTOMERS * 0.08), round(N_CUSTOMERS * 0.06)
    risky_industries = ["Seafood", "Real Estate", "Real Estate", "Textiles", "Retail", "Logistics", "Agriculture"]
    n = 2
    for _ in range(n_def):
        profiles.append((n, "defaulter", random.randint(3, 9), random.choice(risky_industries)))
        n += 1
    for _ in range(n_fp):  # technical delays that trip the legacy rule while fundamentals stay healthy
        profiles.append((n, "false_positive", random.randint(4, 14), random.choice(INDUSTRIES)))
        n += 1
    for _ in range(n_stress):  # temporary stress, recovers
        profiles.append((n, "stressed", random.randint(4, 12), random.choice(INDUSTRIES)))
        n += 1
    while len(profiles) < N_CUSTOMERS:
        profiles.append((n, "healthy", 0, random.choice(INDUSTRIES)))
        n += 1
    rest = profiles[1:]
    random.shuffle(rest)  # Customer A stays first so its story is stable
    profiles = profiles[:1] + rest

    for key, profile, onset, industry in profiles:
        cid = "C_A" if key == "A" else f"C{key:04d}"
        name = "Customer A" if key == "A" else company_name(industry, used_names)
        base_rev = 12000 if key == "A" else random.choice([2000, 5000, 8000, 15000, 30000])
        snaps, default_idx = gen_customer_series(profile, onset, base_rev)
        legacy_idx = next((i for i, s in enumerate(snaps) if s["dpd"] >= LEGACY_DPD), None)
        customers.append({
            "id": cid, "name": name, "profile": profile, "industry": industry,
            "segment": random.choice(SEGMENTS) if key != "A" else "SME",
            "default_date": MONTHS[default_idx] if default_idx is not None else None,
            "legacy_flag_date": MONTHS[legacy_idx] if legacy_idx is not None else None,
            "is_scenario": key == "A",
        })
        for s in snaps:
            cust_snaps.append({**s, "owner": cid, "id": f"{cid}-{s['month']}"})

        # Loan + collateral (+ valuation history)
        lid, kid = f"L{cid}", f"K{cid}"
        amount = round(base_rev * random.uniform(1.5, 4))
        loans.append({"id": lid, "customer": cid, "amount": amount,
                      "disbursed": MONTHS[0], "number": 123 if key == "A" else None})
        collaterals.append({"id": kid, "loan": lid, "kind": random.choice(["Real Estate", "Machinery", "Inventory"])})
        v = amount * random.uniform(1.2, 1.6)
        drop_idx = onset + 4 if profile == "defaulter" and (key == "A" or random.random() < 0.7) else None
        for i in range(N_MONTHS):
            if drop_idx is not None and i == drop_idx:
                v *= 0.90
            val = v * random.uniform(0.995, 1.005)
            valuations.append({"id": f"{kid}-{MONTHS[i]}", "collateral": kid, "date": MONTHS[i], "value": round(val, 1)})

        # Related companies (1-2)
        picks = random.sample([c for c in comp_pool if c != "CP002"], random.choice([1, 2]))
        if key == "A":
            picks = ["CP002"]  # "Related company B" in the demo story
        elif profile in ("healthy", "stressed") and shared_with_b < 7 and random.random() < 0.02:
            picks = picks + ["CP002"]  # a few other borrowers depend on Customer A's related company: contagion demo
            shared_with_b += 1
        for cp in picks:
            related.append({"customer": cid, "company": cp, "since": MONTHS[0]})
        if profile == "defaulter" and (key == "A" or random.random() < 0.7):
            comp_overdue[picks[0]] = min(onset + 3, N_MONTHS - 1)
            events.append({
                "id": f"E-{cid}-REL", "type": "related_overdue", "date": MONTHS[min(onset + 3, N_MONTHS - 1)],
                "description": f"{comp_name[picks[0]]} became past due", "target": cid, "label": "Customer"})
        if random.random() < 0.3 or key == "A":
            guarantees.append({"guarantor": random.choice(guarantors)["id"], "loan": lid})

        # Other risk events
        if profile == "defaulter":
            if drop_idx is not None:
                events.append({"id": f"E-{cid}-COL", "type": "collateral_revalued", "date": MONTHS[min(drop_idx, N_MONTHS - 1)],
                               "description": "Collateral revalued, down 10%", "target": kid, "label": "Collateral"})
            events.append({"id": f"E-{cid}-REV", "type": "revenue_drop", "date": MONTHS[min(onset + 3, N_MONTHS - 1)],
                           "description": "90-day revenue dropped sharply", "target": cid, "label": "Customer"})

    for cp in comp_pool:
        comp_snaps += [{**s, "owner": cp, "id": f"{cp}-{s['month']}"} for s in company_series(comp_overdue.get(cp))]

    # Industry trends: Seafood declines from 2026-01 (idx 9)
    trends = []
    for ind in INDUSTRIES:
        for i in range(N_MONTHS):
            g = random.uniform(-1.0, 3.0)
            if ind == "Seafood" and i >= 9:
                g = -4.0 - 1.5 * (i - 9) * 0.3 + random.uniform(-0.5, 0.5)
            trends.append({"id": f"{ind}-{MONTHS[i]}", "industry": ind, "month": MONTHS[i], "growth_pct": round(g, 1)})
    events.append({"id": "E-IND-TS", "type": "industry_downturn", "date": MONTHS[9],
                   "description": "Seafood industry in downturn (exports falling)", "target": "Seafood", "label": "Industry"})

    return dict(customers=customers, loans=loans, collaterals=collaterals, valuations=valuations,
                companies=companies, guarantors=guarantors, cust_snaps=cust_snaps, comp_snaps=comp_snaps,
                related=related, guarantees=guarantees, events=events, trends=trends)


def run_rows(query: str, rows: list, size: int = 1000):
    """Runs an UNWIND $rows query in batches so one huge transaction does not stall Aura Free."""
    for i in range(0, len(rows), size):
        run(query, rows=rows[i:i + size])


def load(d):
    print("wiping database...", flush=True)
    run("MATCH (n) DETACH DELETE n")
    for label in ["Customer", "Loan", "Collateral", "Company", "Guarantor", "Industry", "Segment",
                  "Snapshot", "Valuation", "IndustryTrend", "RiskEvent", "Policy"]:
        run(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")

    run("UNWIND $rows AS r MERGE (i:Industry {id: r}) SET i.name = r", rows=INDUSTRIES)
    run("UNWIND $rows AS r MERGE (s:Segment {id: r}) SET s.name = r", rows=SEGMENTS)
    run("UNWIND $rows AS r CREATE (:Policy {id: r.id, title: r.title, text: r.text})",
        rows=[{"id": a, "title": b, "text": c} for a, b, c in POLICIES])
    run_rows("""UNWIND $rows AS r
           MERGE (c:Customer {id: r.id})
           SET c.name = r.name, c.profile = r.profile, c.is_scenario = r.is_scenario,
               c.default_date = CASE WHEN r.default_date IS NULL THEN NULL ELSE date(r.default_date) END,
               c.legacy_flag_date = CASE WHEN r.legacy_flag_date IS NULL THEN NULL ELSE date(r.legacy_flag_date) END
           WITH c, r
           MATCH (i:Industry {id: r.industry}), (s:Segment {id: r.segment})
           MERGE (c)-[:OPERATES_IN]->(i) MERGE (c)-[:IN_SEGMENT]->(s)""", rows=d["customers"])
    run_rows("""UNWIND $rows AS r
           MATCH (c:Customer {id: r.customer})
           CREATE (l:Loan {id: r.id, amount: r.amount, disbursed: date(r.disbursed), number: r.number})
           CREATE (c)-[:BORROWS {valid_from: date(r.disbursed)}]->(l)""", rows=d["loans"])
    run_rows("""UNWIND $rows AS r
           MATCH (l:Loan {id: r.loan})
           CREATE (k:Collateral {id: r.id, kind: r.kind})
           CREATE (l)-[:SECURED_BY]->(k)""", rows=d["collaterals"])
    run_rows("""UNWIND $rows AS r
           MATCH (k:Collateral {id: r.collateral})
           CREATE (k)-[:HAS_VALUATION]->(:Valuation {id: r.id, date: date(r.date), value: r.value})""",
        rows=d["valuations"])
    run("UNWIND $rows AS r CREATE (:Company {id: r.id, name: r.name})", rows=d["companies"])
    run("UNWIND $rows AS r CREATE (:Guarantor {id: r.id, name: r.name})", rows=d["guarantors"])
    run_rows("""UNWIND $rows AS r
           MATCH (c:Customer {id: r.customer}), (p:Company {id: r.company})
           CREATE (c)-[:RELATED_TO {valid_from: date(r.since)}]->(p)""", rows=d["related"])
    run_rows("""UNWIND $rows AS r
           MATCH (g:Guarantor {id: r.guarantor}), (l:Loan {id: r.loan})
           MERGE (g)-[:GUARANTEES]->(l)""", rows=d["guarantees"])
    for key, rows in (("Customer", d["cust_snaps"]), ("Company", d["comp_snaps"])):
        for i in range(0, len(rows), 500):
            run(f"""UNWIND $rows AS r
                    MATCH (o:{key} {{id: r.owner}})
                    CREATE (o)-[:HAS_SNAPSHOT]->(:Snapshot {{id: r.id, month: date(r.month), revenue: r.revenue,
                            net_cashflow: r.net_cashflow, dpd: r.dpd}})""", rows=rows[i:i + 500])
    run_rows("""UNWIND $rows AS r
           MATCH (i:Industry {id: r.industry})
           CREATE (i)-[:HAS_TREND]->(:IndustryTrend {id: r.id, month: date(r.month), growth_pct: r.growth_pct})""",
        rows=d["trends"])
    for label in ["Customer", "Collateral", "Industry"]:
        run(f"""UNWIND $rows AS r
                MATCH (t:{label} {{id: r.target}})
                CREATE (e:RiskEvent {{id: r.id, type: r.type, date: date(r.date), description: r.description}})
                CREATE (e)-[:AFFECTS]->(t)""", rows=[e for e in d["events"] if e["label"] == label])


def summarize():
    for q in ["MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC",
              "MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n ORDER BY n DESC"]:
        for row in run(q):
            print(row)
    print(run("""MATCH (c:Customer) RETURN c.profile AS profile, count(*) AS n,
                 count(c.default_date) AS defaulted, count(c.legacy_flag_date) AS legacy_flagged"""))


if __name__ == "__main__":
    data = build()
    print(f"built {len(data['customers'])} customers, loading...", flush=True)
    load(data)
    summarize()
