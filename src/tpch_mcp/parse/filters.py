"""Filter definitions (sFilter JSON from /browse/reload_page_content) and name -> id resolution."""
import re
import unicodedata
from dataclasses import dataclass

TYPE_LABEL = {"or": "any of", "and": "all of", "bool": "yes/no", "min": "at least",
              "max": "at most", "range": "from-to"}
_SKIP_VALUE_IDS = ("", "egal")

# unit -> (base unit, factor)
_UNITS = {
    "b": ("b", 1), "kb": ("b", 1e3), "mb": ("b", 1e6), "gb": ("b", 1e9), "tb": ("b", 1e12), "pb": ("b", 1e15),
    "hz": ("hz", 1), "khz": ("hz", 1e3), "mhz": ("hz", 1e6), "ghz": ("hz", 1e9),
    "bps": ("bps", 1), "kbps": ("bps", 1e3), "mbps": ("bps", 1e6), "gbps": ("bps", 1e9),
    "mm": ("m", 1e-3), "cm": ("m", 1e-2), "m": ("m", 1), "g": ("g", 1), "kg": ("g", 1e3),
    "w": ("w", 1), "kw": ("w", 1e3), "mah": ("mah", 1), "wh": ("wh", 1), "v": ("v", 1),
    '"': ("in", 1), "in": ("in", 1), "inch": ("in", 1), "zoll": ("in", 1),
}


class FilterError(ValueError):
    pass


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.\"]+", " ", s.lower())).strip()


def quantity(label: str) -> tuple[float, str] | None:
    """'2.5 TB' -> (2.5e12, 'b'); '575 W' -> (575, 'w'); '2 ×' -> (2, ''); '3' -> (3, '')."""
    m = re.search(r"(-?\d+(?:[.,]\d+)?)\s*([a-zA-Z\"]*)", str(label).replace("'", ""))
    if not m:
        return None
    num = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    if unit in _UNITS:
        base, f = _UNITS[unit]
        return num * f, base
    return num, unit


@dataclass
class FilterDef:
    id: str
    name: str
    type: str
    unit: str | None
    expert: bool
    values: list[dict]  # {"id","label","hits","selected"}


class FilterSet:
    def __init__(self, raw: dict):
        self.raw = raw
        self.filters = [
            FilterDef(
                id=f["sID"], name=f["sTitle"], type=f["eType"], unit=f.get("sUnitType") or None,
                expert=bool(f.get("bIsExpert")),
                values=[{"id": v["sID"], "label": v["sText"], "hits": v.get("iHits", 0),
                         "selected": bool(v.get("bIsSelected"))}
                        for v in f.get("aValues", []) if v["sID"] not in _SKIP_VALUE_IDS])
            for f in raw.get("aFilter", [])
        ]
        self.order = [f.id for f in self.filters]
        self.brands = [{"id": b["iID"], "name": b["sName"], "hits": b.get("iHits", 0),
                        "selected": bool(b.get("bIsSelected"))} for b in raw.get("aManufacturer", [])]
        self.matches = raw.get("iMatchingsCount")
        self.echo = raw.get("sSerializedFilters", "")

    # ---------- compact view for the model ----------
    def compact(self, include_expert: bool = False, max_values: int = 25) -> dict:
        out = []
        for f in self.filters:
            if f.expert and not include_expert:
                continue
            vals = [v for v in f.values if v["hits"] > 0 or v["selected"]]
            if not vals:
                continue
            entry = {"name": f.name, "type": TYPE_LABEL.get(f.type, f.type)}
            if f.type in ("min", "max", "range"):
                entry["values"] = [v["label"] for v in vals]  # ordered scale, hits less useful
                if len(vals) > max_values:
                    step = len(vals) / max_values
                    entry["values"] = [vals[int(i * step)]["label"] for i in range(max_values)] + [vals[-1]["label"]]
                    entry["note"] = f"{len(vals)} steps, sampled; any value in range is accepted"
            elif f.type == "bool":
                pass
            else:
                top = sorted(vals, key=lambda v: -v["hits"])[:max_values]
                entry["values"] = [f'{v["label"]} ({v["hits"]})' for v in top]
                if len(vals) > max_values:
                    entry["more_values"] = len(vals) - max_values
            if any(v["selected"] for v in f.values):
                entry["selected"] = [v["label"] for v in f.values if v["selected"]]
            out.append(entry)
        hidden = sum(1 for f in self.filters if f.expert) if not include_expert else 0
        pr = self.raw.get("aPriceRange") or {}
        cy = self.raw.get("aCreationYear") or {}
        brands = sorted([b for b in self.brands if b["hits"] > 0 or b["selected"]], key=lambda b: -b["hits"])
        res = {
            "matches": self.matches,
            "filters": out,
            "brands": [f'{b["name"]} ({b["hits"]})' for b in brands[:40]],
            "price_max": pr.get("iMaxPrice"),
            "release_years": [cy.get("iSelectableMinYear"), cy.get("iSelectableMaxYear")],
        }
        if len(brands) > 40:
            res["more_brands"] = len(brands) - 40
        if hidden:
            res["expert_filters_hidden"] = hidden
        if not out:
            res["note"] = "No spec filters here - pick a leaf category (see toppreise_list_categories / toppreise_find_category)."
        return res

    def values_of(self, filter_name: str, search: str | None = None) -> dict:
        f = self.find_filter(filter_name)
        vals = f.values
        if search:
            q = _norm(search)
            vals = [v for v in vals if q in _norm(v["label"])]
        return {"filter": f.name, "type": TYPE_LABEL.get(f.type, f.type),
                "values": [f'{v["label"]} ({v["hits"]})' for v in vals]}

    # ---------- resolution ----------
    def find_filter(self, name: str) -> FilterDef:
        if name in self.order:
            return self.filters[self.order.index(name)]
        q = _norm(name)
        exact = [f for f in self.filters if _norm(f.name) == q]
        if exact:
            return exact[0]
        part = [f for f in self.filters if q in _norm(f.name) or _norm(f.name) in q]
        if len(part) == 1:
            return part[0]
        names = [f.name for f in self.filters]
        raise FilterError(f"Unknown filter {name!r}. Available filters: {names}" if not part else
                          f"Ambiguous filter {name!r}: {[f.name for f in part]}")

    def _match_value(self, f: FilterDef, wanted) -> dict:
        w = str(wanted)
        if w in [v["id"] for v in f.values]:
            return next(v for v in f.values if v["id"] == w)
        q = _norm(w)
        exact = [v for v in f.values if _norm(v["label"]) == q]
        if exact:
            return exact[0]
        words = q.split()
        part = [v for v in f.values if all(x in _norm(v["label"]).split() or x in _norm(v["label"]) for x in words)]
        if len(part) == 1:
            return part[0]
        if len(part) > 1:
            # prefer the shortest label containing all words (e.g. "RTX 5090" vs "RTX 5090 D")
            part.sort(key=lambda v: (len(v["label"]), -v["hits"]))
            best = [v for v in part if all(x in _norm(v["label"]).split() for x in words)]
            if len(best) == 1:
                return best[0]
            raise FilterError(f"{f.name}: {wanted!r} is ambiguous: {[v['label'] for v in part[:15]]}")
        sample = [v["label"] for v in sorted(f.values, key=lambda v: -v["hits"])[:30]]
        raise FilterError(f"{f.name}: no value matches {wanted!r}. Options include: {sample}")

    def _threshold(self, f: FilterDef, wanted, direction: str) -> dict:
        """Pick a scale value: 'up' = smallest value >= wanted, 'down' = largest value <= wanted."""
        scale = [(quantity(v["label"]), v) for v in f.values]
        scale = [(q, v) for q, v in scale if q is not None]
        if not scale:
            return self._match_value(f, wanted)
        raw_units = {(re.search(r"[a-zA-Z\"]+", v["label"]) or [""])[0].lower() for _, v in scale}
        target = quantity(str(wanted))
        if target is None:
            raise FilterError(f"{f.name}: cannot read a number from {wanted!r}")
        tnum, tunit = target
        has_unit = bool(re.search(r"[a-zA-Z\"]", str(wanted)))
        if has_unit:
            cands = [(n, v) for (n, u), v in scale]          # compare in base units (GB vs TB ok)
        elif len(raw_units) == 1:
            cands = [(quantity(re.sub(r"[a-zA-Z\"].*", "", v["label"]))[0], v) for _, v in scale]
        else:
            raise FilterError(f"{f.name}: values use several units {sorted(raw_units)}; "
                              f"give a unit, e.g. '{scale[len(scale) // 2][1]['label']}'")
        cands.sort(key=lambda c: c[0])
        if direction == "up":
            ok = [c for c in cands if c[0] >= tnum - 1e-9]
            if not ok:
                raise FilterError(f"{f.name}: nothing at or above {wanted!r} (max is {cands[-1][1]['label']})")
            return min(ok, key=lambda c: c[0])[1]
        ok = [c for c in cands if c[0] <= tnum + 1e-9]
        if not ok:
            raise FilterError(f"{f.name}: nothing at or below {wanted!r} (min is {cands[0][1]['label']})")
        return max(ok, key=lambda c: c[0])[1]

    def resolve(self, spec: dict) -> tuple[list[tuple[str, str, str]], list[str]]:
        """spec: {filter name or id: value | [values] | {"min": x, "max": y} | true}.
        Returns (props for sfh, human readable list of what was applied)."""
        props: list[tuple[str, str, str]] = []
        applied: list[str] = []
        for name, wanted in (spec or {}).items():
            f = self.find_filter(name)
            if f.type == "bool":
                if wanted in (False, "no", "No", 0):
                    continue
                v = f.values[0] if f.values else None
                if v is None:
                    raise FilterError(f"{f.name}: no selectable value")
                props.append(("bool", f.id, v["id"]))
                applied.append(f"{f.name} = yes")
            elif f.type in ("or", "and"):
                vals = wanted if isinstance(wanted, (list, tuple)) else [wanted]
                for w in vals:
                    v = self._match_value(f, w)
                    props.append((f.type, f.id, v["id"]))
                labels = [self._match_value(f, w)["label"] for w in vals]
                applied.append(f"{f.name}: {(' or ' if f.type == 'or' else ' and ').join(labels)}")
            elif f.type in ("min", "max", "range"):
                if not isinstance(wanted, dict):
                    wanted = {"min": wanted} if f.type == "min" else {"max": wanted} if f.type == "max" else {"min": wanted}
                lo, hi = wanted.get("min"), wanted.get("max")
                if f.type == "min" and hi is not None:
                    raise FilterError(f"{f.name} only supports a minimum")
                if f.type == "max" and lo is not None:
                    raise FilterError(f"{f.name} only supports a maximum")
                if lo is not None:
                    v = self._threshold(f, lo, "up")
                    props.append(("min" if f.type == "min" else "range_from", f.id, v["id"]))
                    applied.append(f"{f.name} >= {v['label']}")
                if hi is not None:
                    v = self._threshold(f, hi, "down")
                    props.append(("max" if f.type == "max" else "range_to", f.id, v["id"]))
                    applied.append(f"{f.name} <= {v['label']}")
            else:
                raise FilterError(f"{f.name}: unsupported filter type {f.type!r}")
        return props, applied

    def brand_ids(self, names: list[str] | None) -> tuple[list[int], list[str]]:
        ids, labels = [], []
        for n in names or []:
            if isinstance(n, int) or str(n).isdigit():
                b = next((b for b in self.brands if b["id"] == int(n)), None)
            else:
                q = _norm(n)
                b = next((b for b in self.brands if _norm(b["name"]) == q), None) or \
                    next((b for b in self.brands if q in _norm(b["name"])), None)
            if b is None:
                top = [b["name"] for b in sorted(self.brands, key=lambda b: -b["hits"])[:30]]
                raise FilterError(f"Unknown brand {n!r} in this category. Brands include: {top}")
            ids.append(b["id"])
            labels.append(b["name"])
        return ids, labels
