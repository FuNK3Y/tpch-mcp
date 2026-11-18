import re

from selectolax.lexbor import LexborNode

BASE = "https://www.toppreise.ch"

AVAILABILITY = {
    1: "in stock",
    2: "available from external/foreign warehouse",
    3: "up to one week",
    4: "up to two weeks",
    7: "upon request",
    8: "cannot be delivered at this time",
    9: "no information",
}


def text(node: LexborNode | None) -> str | None:
    if node is None:
        return None
    t = re.sub(r"\s+", " ", node.text(separator=" ")).strip()
    return t or None


def price(s: str | None) -> float | None:
    """Parse Swiss formatted prices like "7'074.45"."""
    if not s:
        return None
    s = s.replace("'", "").replace("’", "").replace(" ", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


def avail_level(node: LexborNode | None) -> int | None:
    if node is None:
        return None
    icon = node.css_first('[class*="TPIcons-avail_"]')
    if icon is None:
        return None
    m = re.search(r"TPIcons-avail_(\d+)", icon.attributes.get("class") or "")
    return int(m.group(1)) if m else None


def absolute(url: str | None) -> str | None:
    if not url:
        return None
    return url if url.startswith("http") else BASE + url
