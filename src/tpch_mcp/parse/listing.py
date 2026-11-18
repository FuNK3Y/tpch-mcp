"""Category / search result lists (all three views: list, grid ("box"), table ("expert"))."""
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .util import AVAILABILITY, absolute, avail_level, image_url, price, text

CARD_CLASSES = ("mixedBrowsingList", "mixedBrowsingExpert", "mixedBrowsingBox")
_CARD_SEL = ", ".join(f"div.{c}" for c in CARD_CLASSES)


def _is_card(n: LexborNode) -> bool:
    cls = n.attributes.get("class") or ""
    return any(c in cls for c in CARD_CLASSES)


def _is_top(n: LexborNode) -> bool:
    p = n.parent
    while p is not None:
        if _is_card(p):
            return False
        p = p.parent
    return True


def _ppu(n: LexborNode) -> dict | None:
    e, u = n.css_first(".shippingPricePerUnit"), n.css_first(".divider")
    if e is None or u is None:
        return None
    return {"price": price(text(e)), "unit": (text(u) or "").lstrip("/ ") or None}


def _product_row(n: LexborNode) -> dict:
    ol = text(n.css_first(".offersLabel")) or ""
    m = re.search(r"(\d+)", ol)
    level = avail_level(n)
    link = n.attributes.get("data-link") or ""
    return {
        "id": int(n.attributes["data-entity-id"]),
        "name": text(n.css_first("a.bold") or n.css_first(".product-name")),
        "summary": text(n.css_first(".product-features")),
        "offer_count": int(m.group(1)) if m else None,
        "price": price(text(n.css_first(".productPrice .Plugin_Price"))),
        "price_incl_shipping": price(text(n.css_first(".shippingPrice .Plugin_Price"))),
        "price_per_unit": _ppu(n),
        "image": image_url(n),
        "availability": AVAILABILITY.get(level) if level else None,
        "url": absolute(link.split("?")[0]) if link else None,
    }


def _offer_card(c: LexborNode) -> dict:
    prices = [price(text(x)) for x in c.css(".Plugin_Price")]
    ships = [price(text(x)) for x in c.css("span.ml-1")]
    rt = c.css_first(".AbstractTooltip_ShopRatingDetailsTooltip")
    m = re.search(r"for\s*(.+?):\s*([\d.]+)\s*$", (rt.attributes.get("title") or "") if rt else "")
    level = avail_level(c)
    return {
        "type": "offer",
        "offer_id": int(c.attributes["data-entity-id"]),
        "dealer_id": int(c.attributes.get("data-dealer-id") or 0) or None,
        "title": text(c.css_first("a.offer-name")),
        "shop": m.group(1).strip() if m else None,
        "shop_rating": (float(m.group(2)) or None) if m else None,
        "price": prices[0] if prices else None,
        "shipping": ships[0] if ships else None,
        "total": prices[1] if len(prices) > 1 else None,
        "availability": AVAILABILITY.get(level) if level else None,
        "url": absolute(c.attributes.get("data-link")),
    }


def parse_listing(html: str) -> dict:
    tree = LexborHTMLParser(html)
    items: list[dict] = []
    for c in tree.css(_CARD_SEL):
        if not _is_top(c):
            continue
        cid = c.attributes.get("id") or ""
        if cid.startswith("Plugin_ProductCollItem_"):
            link = c.css_first('a[href*="/productcollection/"]')
            items.append({
                "type": "series",
                "id": c.attributes.get("data-entity-id"),
                "name": text(c.css_first("div.bold")),
                "summary": text(c.css_first(".product-features")),
                "url": absolute(link.attributes.get("href")) if link else None,
                "image": image_url(c),
                "variants": [_product_row(v) for v in c.css("div.Plugin_Product[data-link]")
                             if v.attributes.get("data-entity-id")],
            })
        elif cid.startswith("Plugin_Product_") and c.attributes.get("data-entity-id"):
            items.append({"type": "product", **_product_row(c)})
        elif cid.startswith("Plugin_Offer_") and c.attributes.get("data-entity-id"):
            items.append(_offer_card(c))

    page = pages = 1
    sel = tree.css_first('select[aria-label^="Page "]')
    if sel is not None:
        m = re.search(r"Page (\d+) of (\d+)", sel.attributes.get("aria-label") or "")
        if m:
            page, pages = int(m.group(1)), int(m.group(2))
    page_size = None
    pps = tree.css_first("input.f_PaginationItemsPerPage_NoOfItems")
    if pps is not None and (pps.attributes.get("value") or "").isdigit():
        page_size = int(pps.attributes["value"])
    return {"page": page, "pages": pages, "page_size": page_size, "items": items}
