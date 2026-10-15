"""PII guard between the bank's data and the LLM.

Names of customers, related companies and guarantors are replaced by stable tokens (CUSTOMER_0007) before any text
leaves the service, and restored in the model's answer. Internal ids (C_A, CP002) are kept: they carry no personal data
and are what the evidence links use. In this prototype all names are synthetic, but the layer is what a real bank
deployment would need on day one.
"""
import json
import re
from typing import Any

_cache: dict[int, "Pseudonymizer"] = {}


class Pseudonymizer:
    def __init__(self, history: dict):
        names: dict[str, str] = {}
        for c in history["customers"].values():
            names.setdefault(c["name"], f"CUSTOMER_{len(names):04d}")
            for r in c["related"]:
                names.setdefault(r["name"], f"COMPANY_{len(names):04d}")
            for g in c["guarantors"]:
                names.setdefault(g["name"], f"GUARANTOR_{len(names):04d}")
        self.to_token = names
        self.to_name = {t: n for n, t in names.items()}
        self._mask = re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)))
        self._unmask = re.compile(r"\b(?:CUSTOMER|COMPANY|GUARANTOR)_\d{4}\b")
        self.masked = 0  # replacements made since creation, shown in the UI as proof the guard ran

    def mask_text(self, text: str) -> str:
        out, n = self._mask.subn(lambda m: self.to_token[m.group(0)], text)
        self.masked += n
        return out

    def mask(self, obj: Any) -> Any:
        """Masks every string inside a JSON-compatible structure."""
        return json.loads(self.mask_text(json.dumps(obj, ensure_ascii=False, default=str)))

    def unmask_text(self, text: str) -> str:
        return self._unmask.sub(lambda m: self.to_name.get(m.group(0), m.group(0)), text)

    def unmask(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.unmask_text(obj)
        if isinstance(obj, list):
            return [self.unmask(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.unmask(v) for k, v in obj.items()}
        return obj


def for_history(history: dict) -> Pseudonymizer:
    key = id(history)
    if key not in _cache:
        _cache.clear()
        _cache[key] = Pseudonymizer(history)
    return _cache[key]
