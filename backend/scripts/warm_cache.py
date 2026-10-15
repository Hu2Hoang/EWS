"""Pre-runs Gemini for the demo checkpoints and stores results in backend/.cache.
Run: .venv/bin/python -m scripts.warm_cache"""
import sys

from app import repository
from app.agent import cached, explain

CUSTOMER = "C_A"
MONTHS = ["2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01", "2026-09-01"]

if __name__ == "__main__":
    h = repository.load_all()
    for m in MONTHS:
        if cached(h, CUSTOMER, m):
            print(m, "already cached"); continue
        r = explain(h, CUSTOMER, m)
        print(m, r["source"], r["risk_level"], len(r["tool_calls"]), "tools", "OK" if r["verification"]["ok"] else r["verification"]["issues"])
        sys.stdout.flush()
