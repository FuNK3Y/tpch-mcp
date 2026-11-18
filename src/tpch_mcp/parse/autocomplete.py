"""GET /plugins/generalelements/JsAutocomplete?type=search&q=... (JSON)."""
import json
import re
from urllib.parse import parse_qs, urlsplit

from .util import absolute


def parse_autocomplete(raw: str | dict) -> dict:
    data = json.loads(raw) if isinstance(raw, str) else raw
    cats, terms = [], []
    for r in (data.get("data") or {}).get("result", []):
        href = r.get("href") or ""
        if r.get("type") == "c":
            m = re.search(r"-(c\d+)(?:\?|$)", href)
            if r.get("category") == "selected category":
                continue
            cats.append({"category": r.get("category"), "category_id": m.group(1) if m else None,
                         "query": r.get("searchterm"), "url": absolute(href)})
        else:
            q = parse_qs(urlsplit(href).query).get("q", [r.get("searchterm")])[0]
            terms.append(q)
    return {"categories": cats, "suggested_queries": terms}
