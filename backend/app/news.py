"""News to graph: Gemini reads a headline or article (English or Vietnamese), extracts the credit-risk events and the
entities they hit, and each entity is linked to a node of the Knowledge Graph. Nothing changes until an officer confirms
an event; it then becomes a live overlay (see overlays.py) and the portfolio is re-scored.

Linking reuses the PII guard: known names are replaced by tokens before the text reaches Gemini, so an entity Gemini
reports comes back as a token that maps to exactly one node. Names the guard did not catch (typos, other spellings) are
matched by similarity. Without Gemini a keyword extractor does the same job, less well.
"""
import difflib
import json
import os
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app import contagion, live, privacy
from app.overlays import EVENT_TYPES
from app.scoring import month_floor

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

EventType = Enum("EventType", {k: k for k in EVENT_TYPES}, type=str)


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class EntityType(str, Enum):
    Company = "Company"
    Customer = "Customer"
    Industry = "Industry"


class Extracted(BaseModel):
    entity: str = Field(description="The affected entity as written in the text. Keep tokens such as COMPANY_0012 exactly as "
                                    "they are. For an industry use one of the listed industry names.")
    entity_type: EntityType
    event_type: EventType
    severity: Severity = Field(description="low: minor or uncertain; medium: material; high: default, insolvency or a large loss.")
    summary: str = Field(description="One short English sentence describing the event for the bank's risk log.")
    quote: str = Field(description="The phrase of the text that states this event.")


class NewsAnalysis(BaseModel):
    events: list[Extracted]


SYSTEM = """You extract credit-risk events from news for a bank's early-warning system.
Rules:
- Only events the text states. Never guess an entity that is not named in the text.
- The text may be English or Vietnamese; always answer in English.
- Company and person names may appear as tokens (COMPANY_0012, CUSTOMER_0003). Return the token unchanged as `entity`.
- A sector-wide event (tariffs, a market slump, a drought) is an Industry event; use one of the listed industries.
- One entry per affected entity. Return an empty list if the text holds no credit-risk event."""

INDUSTRY_WORDS = {
    "Seafood": ["seafood", "fishery", "shrimp", "pangasius", "thủy sản", "hải sản", "tôm", "cá tra"],
    "Real Estate": ["real estate", "property", "housing", "bất động sản", "nhà ở", "địa ốc"],
    "Textiles": ["textile", "garment", "apparel", "dệt may", "may mặc"],
    "Logistics": ["logistics", "freight", "shipping", "port", "vận tải", "cảng", "logistic"],
    "Agriculture": ["agricultur", "farm", "rice", "coffee", "harvest", "drought", "nông nghiệp", "lúa", "cà phê", "hạn hán"],
    "Retail": ["retail", "consumer spending", "bán lẻ", "tiêu dùng"],
}
KEYWORDS = [  # first match wins; English and Vietnamese
    ("bankruptcy", ["bankrupt", "insolven", "phá sản", "giải thể", "mất khả năng thanh toán"]),
    ("payment_default", ["default", "missed", "misses", "overdue", "past due", "chậm trả", "nợ quá hạn", "chậm thanh toán", "vỡ nợ"]),
    ("tax_enforcement", ["tax", "thuế"]),
    ("legal_action", ["lawsuit", "sued", "court", "khởi kiện", "tòa án", "bị kiện"]),
    ("collateral_impairment", ["collateral", "tài sản bảo đảm", "tài sản thế chấp"]),
    ("revenue_loss", ["fire", "lost contract", "shutdown", "halt", "strike", "cháy", "mất hợp đồng", "ngừng sản xuất", "đình công", "sụt giảm doanh thu"]),
    ("industry_downturn", ["anti-dumping", "tariff", "duties", "slump", "downturn", "drought", "collapse", "chống bán phá giá", "suy thoái", "đóng băng", "giảm mạnh"]),
]
HIGH_WORDS = ["bankrupt", "insolven", "default", "halt", "fire", "collapse", "phá sản", "vỡ nợ", "cháy", "ngừng", "đóng băng"]
LOW_WORDS = ["may ", "might", "could", "rumou", "rumor", "considering", "có thể", "tin đồn", "xem xét"]


def _names(history: dict) -> dict[str, tuple[str, str, str]]:
    """lower-case name -> (type, id, name) for every customer, related company and industry."""
    out = {}
    for cid, c in history["customers"].items():
        out[c["name"].lower()] = ("Customer", cid, c["name"])
        for r in c["related"]:
            out.setdefault(r["name"].lower(), ("Company", r["id"], r["name"]))
    for ind in history["industry_trends"]:
        out[ind.lower()] = ("Industry", ind, ind)
    return out


def link(history: dict, entity: str, entity_type: str = "") -> dict | None:
    names = _names(history)
    key = entity.strip().lower()
    hit, match = names.get(key), "exact"
    if not hit and entity_type == "Industry":
        ind = next((i for i, words in INDUSTRY_WORDS.items() if any(w in key for w in words)), None)
        hit, match = (("Industry", ind, ind), "synonym") if ind else (None, "")
    if not hit:
        close = difflib.get_close_matches(key, list(names), n=1, cutoff=0.82)
        hit, match = (names[close[0]], "similar") if close else (None, "")
    return {"type": hit[0], "id": hit[1], "name": hit[2], "match": match} if hit else None


def _severity(text: str) -> str:
    low = text.lower()
    return "high" if any(w in low for w in HIGH_WORDS) else "low" if any(w in low for w in LOW_WORDS) else "medium"


def _keyword_extract(history: dict, text: str) -> list[dict]:
    low = text.lower()
    kind = next((k for k, words in KEYWORDS if any(w in low for w in words)), None)
    if not kind:
        return []
    found = [(t, i, n) for name, (t, i, n) in _names(history).items() if t != "Industry" and len(name) > 4 and name in low]
    if not found:
        found = [("Industry", i, i) for i, words in INDUSTRY_WORDS.items() if any(w in low for w in words)][:1]
    sev = _severity(text)
    return [{"entity": n, "entity_type": t, "event_type": "industry_downturn" if t == "Industry" else kind, "severity": sev,
             "summary": f"{n}: {EVENT_TYPES['industry_downturn' if t == 'Industry' else kind].lower()} reported in the news.",
             "quote": text[:160]} for t, i, n in found[:4]]


def analyze(history: dict, text: str, as_of: str) -> dict:
    as_of = month_floor(as_of)
    guard = privacy.for_history(history)
    masked_before = guard.masked
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        resp = client.models.generate_content(
            model=model,
            contents=f"Industries: {', '.join(sorted(history['industry_trends']))}\n\nText:\n{guard.mask_text(text)}",
            config=types.GenerateContentConfig(system_instruction=SYSTEM, temperature=0.1,
                                               response_mime_type="application/json", response_schema=NewsAnalysis))
        found = guard.unmask(json.loads(resp.text)["events"])
        source = "gemini"
    except Exception as e:  # no key, quota, network: the keyword extractor keeps the feature working
        print(f"news extraction fallback: {type(e).__name__}: {str(e)[:160]}", flush=True)
        found, source = _keyword_extract(history, text), "keywords"

    out, seen = [], set()
    for e in found:
        e = {k: (v.value if isinstance(v, Enum) else v) for k, v in e.items()}
        node = link(history, e["entity"], e["entity_type"])
        item = {**e, "linked": node, "draft": None, "preview": None}
        if node:
            if (node["id"], e["event_type"]) in seen:
                continue
            seen.add((node["id"], e["event_type"]))
            draft = live.new_event(as_of, node["type"], node["id"], e["event_type"], e["severity"],
                                   e["summary"], source, text)
            item["draft"], item["preview"] = draft, live.preview(draft)
        out.append(item)
    return {"as_of": as_of, "source": source, "model": model if source == "gemini" else None, "events": out,
            "privacy": {"pii_masked": source == "gemini", "replacements": guard.masked - masked_before}}


INDUSTRY_NEWS = {
    "Seafood": "EU raises anti-dumping duties on Vietnamese seafood; shrimp and pangasius export orders expected to fall sharply.",
    "Real Estate": "Thị trường bất động sản đóng băng: giao dịch nhà ở quý này giảm 40%, nhiều chủ đầu tư chậm trả nợ trái phiếu.",
    "Textiles": "New US tariffs hit Vietnamese textile exporters; garment orders down 30% year on year.",
    "Logistics": "Freight rates collapse as port volumes fall; logistics firms report a second quarter of losses.",
    "Agriculture": "Drought in the Mekong Delta cuts rice and coffee harvests; farm incomes expected to fall sharply.",
    "Retail": "Consumer spending slumps; retail chains report a sharp fall in same-store sales.",
}


def samples(history: dict, as_of: str) -> list[dict]:
    """Ready-made headlines about entities that exist in this portfolio and are still healthy, so applying one shows a change."""
    as_of = month_floor(as_of)
    nodes, inbound = contagion.build_graph(history, as_of)
    companies = sorted((n for n, v in nodes.items() if v["type"] == "Company" and v["own"] == 0),
                       key=lambda n: -sum(len(x) for x in inbound[n].values()))
    res = contagion.propagate(history, as_of)
    custs = sorted((cid for cid, v in res.items() if 0 < v["own"] < 0.5), key=lambda cid: -history["customers"][cid]["loan_amount"])
    growth = {i: next((t["growth_pct"] for t in reversed(tr) if t["month"] <= as_of), 0) for i, tr in history["industry_trends"].items()}
    industry = max(growth, key=growth.get)
    out = []
    if companies:
        out.append({"label": "Related company insolvent", "text": f"{nodes[companies[0]]['label']} misses a bond coupon payment; "
                                                                  "creditors say the company is insolvent and will file for bankruptcy."})
    if len(companies) > 1:
        out.append({"label": "Tax enforcement (Vietnamese)", "text": f"Cục Thuế ra quyết định cưỡng chế {nodes[companies[1]]['label']} "
                                                                     "do nợ thuế VAT 3 tháng liên tiếp."})
    if custs:
        out.append({"label": "Fire at a borrower's plant", "text": f"A fire has halted production at {history['customers'][custs[0]]['name']}'s "
                                                                   "main plant; output is expected to fall by a third this quarter."})
    out.append({"label": f"{industry} downturn", "text": INDUSTRY_NEWS.get(industry, f"Sharp downturn reported in the {industry} sector.")})
    return out
