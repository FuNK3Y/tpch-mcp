"""Encode/decode toppreise "sfh" filter strings.

Format reverse-engineered from browser captures and verified against the live site (2026-11-16):

    oi~<item>::<item>  +  m~<brand>.<brand>  +  o~<offset>  +  s~<sort>  +  a~<avail>
      +  iy~<year>  +  ip~<cents>  +  xp~<cents>  +  pt~<base64url>  +  e~1

Property items (``<filter prefix><filter id>:<value prefix><value id>``):

    or          o<fid>:s<vid>       several values = repeated items (``v1.v2`` is silently dropped)
    and         a<fid>:s<vid>
    bool        b<fid>:b<vid>
    min         f<fid>:f<vid>
    max         t<fid>:t<vid>
    range from  r<fid>:f<vid>
    range to    r<fid>:t<vid>

Items are ordered by the category's filter definition order; brand ids ascending.
The server silently drops anything it does not understand, so callers must compare the
echoed ``sSerializedFilters`` with what they sent (see :func:`same_selection`).
"""
import base64
from dataclasses import dataclass, field
from urllib.parse import unquote

PROP: dict[str, tuple[str, str]] = {
    "or": ("o", "s"),
    "and": ("a", "s"),
    "bool": ("b", "b"),
    "min": ("f", "f"),
    "max": ("t", "t"),
    "range_from": ("r", "f"),
    "range_to": ("r", "t"),
}
_REV = {v: k for k, v in PROP.items()}

SORTS = {
    "pa": "lowest price", "pd": "highest price", "rd": "most popular", "td": "newest",
    "od": "most offers", "va": "available first", "ba": "name A-Z", "bd": "name Z-A",
}


@dataclass
class Selection:
    props: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, filter_id, value_id)
    manufacturers: list[int] = field(default_factory=list)
    offset: int = 0
    sort: str | None = None
    max_availability: int | None = None
    min_year: int | None = None
    price_min: float | None = None
    price_max: float | None = None
    text: str | None = None
    expert: bool = False
    unknown: dict[str, str] = field(default_factory=dict)


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _unb64(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def encode(sel: Selection, filter_order: list[str] | tuple[str, ...] = ()) -> str:
    order = {fid: i for i, fid in enumerate(filter_order)}
    props = sorted(sel.props, key=lambda p: order.get(p[1], len(order)))
    items = []
    for kind, fid, vid in props:
        if kind not in PROP:
            raise ValueError(f"unsupported filter kind {kind!r}")
        fp, vp = PROP[kind]
        items.append(f"{fp}{fid}:{vp}{vid}")
    toks: list[str] = []
    if items:
        toks.append("oi~" + "::".join(items))
    if sel.manufacturers:
        toks.append("m~" + ".".join(str(m) for m in sorted(set(sel.manufacturers))))
    if sel.offset:
        toks.append(f"o~{sel.offset}")
    if sel.sort:
        toks.append(f"s~{sel.sort}")
    if sel.max_availability:
        toks.append(f"a~{sel.max_availability}")
    if sel.min_year:
        toks.append(f"iy~{sel.min_year}")
    if sel.price_min is not None:
        toks.append(f"ip~{round(sel.price_min * 100)}")
    if sel.price_max is not None:
        toks.append(f"xp~{round(sel.price_max * 100)}")
    if sel.text:
        toks.append("pt~" + _b64(sel.text))
    if sel.expert:
        toks.append("e~1")
    return "+".join(toks)


def decode(s: str) -> Selection:
    s = unquote(s or "")
    sel = Selection()
    for tok in filter(None, s.split("+")):
        k, _, v = tok.partition("~")
        if k == "oi":
            for it in v.split("::"):
                f, _, val = it.partition(":")
                if not f or not val:
                    continue
                kind = _REV.get((f[0], val[0]), f"?{f[0]}{val[0]}")
                sel.props.append((kind, f[1:], val[1:]))
        elif k == "m":
            sel.manufacturers = [int(x) for x in v.split(".") if x]
        elif k == "o":
            sel.offset = int(v)
        elif k == "s":
            sel.sort = v
        elif k == "a":
            sel.max_availability = int(v)
        elif k == "iy":
            sel.min_year = int(v)
        elif k == "ip":
            sel.price_min = int(v) / 100
        elif k == "xp":
            sel.price_max = int(v) / 100
        elif k == "pt":
            sel.text = _unb64(v)
        elif k == "e":
            sel.expert = v == "1"
        else:
            sel.unknown[k] = v
    return sel


def same_selection(sent: str, echoed: str) -> tuple[bool, list[str]]:
    """Compare what we sent with what the server echoed. Returns (ok, list of differences)."""
    a, b = decode(sent), decode(echoed)
    diffs = []
    lost = set(a.props) - set(b.props)
    if lost:
        diffs.append(f"filter values ignored by the site: {sorted(lost)}")
    for attr in ("manufacturers", "sort", "max_availability", "min_year", "price_min", "price_max", "text", "expert"):
        va, vb = getattr(a, attr), getattr(b, attr)
        if attr == "manufacturers":
            va, vb = sorted(va), sorted(vb)
        if va != vb:
            diffs.append(f"{attr}: sent {va!r}, site applied {vb!r}")
    return (not diffs, diffs)
