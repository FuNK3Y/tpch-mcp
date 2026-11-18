"""Product detail page: JSON-LD + offer table + data sheet."""
import json
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .util import AVAILABILITY, absolute, avail_level, price, text


def _jsonld(tree: LexborHTMLParser) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and "@type" in data:
            out[data["@type"]] = data
    return out


def _offer(node: LexborNode) -> dict:
    a = node.attributes
    info = node.css_first(".price_information_offer") or node
    product_price = price(text(info.css_first(".productPrice .Plugin_Price")))
    total = price(text(info.css_first(".shippingPrice .Plugin_Price")))
    ship_node = info.css_first(".productPrice .shippingText span.ml-1")
    shipping = price(text(ship_node)) if ship_node else (
        round(total - product_price, 2) if total is not None and product_price is not None else None)

    shop = rating = rating_count = None
    rt = node.css_first(".AbstractTooltip_ShopRatingDetailsTooltip")
    if rt is not None:
        m = re.search(r"for\s*(.+?):\s*([\d.]+)\s*$", rt.attributes.get("title") or "")
        if m:
            shop, rating = m.group(1).strip(), float(m.group(2)) or None
        m = re.search(r"\(([\d'’]+)\)", text(rt) or "")
        if m:
            rating_count = int(re.sub(r"\D", "", m.group(1)))
    if shop is None:
        logo = node.css_first(".Plugin_ShopLogo img")
        shop = (logo.attributes.get("alt") or logo.attributes.get("title")) if logo else None

    level = avail_level(node)
    av_tip = node.css_first(".AbstractTooltip_AvailabilityInformationTooltip")
    av_text = av_tip.attributes.get("title") if av_tip is not None else None
    if not av_text or "JavaScript" in av_text:
        av_text = AVAILABILITY.get(level) if level else None

    return {
        "shop": shop,
        "dealer_id": int(a["data-dealer-id"]) if a.get("data-dealer-id") else None,
        "offer_id": int(a["data-entity-id"]) if a.get("data-entity-id") else None,
        "title": text(node.css_first("a.offer-name")),
        "price": product_price,
        "shipping": shipping,
        "total": total,
        "availability": {"level": level, "text": av_text},
        "shop_rating": rating,
        "shop_rating_count": rating_count,
        "url": absolute(a.get("data-link")),
    }


def _specs(tree: LexborHTMLParser) -> dict[str, dict[str, str]]:
    specs: dict[str, dict[str, str]] = {}
    for cat in tree.css("div.Plugin_ProductNgfFeatureCategory"):
        name = text(cat.css_first(".featureCatName")) or "other"
        rows = {}
        for feat in cat.css("div.Plugin_ProductNgfFeature"):
            k, v = text(feat.css_first(".name")), text(feat.css_first(".value"))
            if k and v and v != "-":
                rows[k] = v
        if rows:
            specs[name] = rows
    return specs


def parse_product(html: str) -> dict:
    tree = LexborHTMLParser(html)
    ld = _jsonld(tree)
    p = ld.get("Product")
    if not p:
        raise ValueError("no Product JSON-LD found (blocked page or not a product page?)")
    crumbs = (ld.get("BreadcrumbList") or {}).get("itemListElement", [])
    path = [c.get("name") for c in crumbs][1:-1]
    category_id = None
    for c in crumbs:
        m = re.search(r"-(c\d+)$", c.get("item") or "")
        if m:
            category_id = m.group(1)  # deepest category wins
    agg = p.get("offers") or {}
    offers = [_offer(n) for n in tree.css("div.Plugin_Offer.productDetailsOfferList")]
    m = re.search(r"-p(\d+)$", p.get("url") or "")
    return {
        "id": int(m.group(1)) if m else None,
        "name": p.get("name"),
        "brand": (p.get("brand") or {}).get("name"),
        "mpn": p.get("mpn"),
        "gtin": p.get("gtin13") or p.get("gtin"),
        "category_id": category_id,
        "category_path": path,
        "summary": p.get("description"),
        "image": (p.get("image") or [None])[0],
        "url": p.get("url"),
        "price": {
            "currency": agg.get("priceCurrency", "CHF"),
            "min": float(agg["lowPrice"]) if agg.get("lowPrice") else None,
            "max": float(agg["highPrice"]) if agg.get("highPrice") else None,
            "offer_count": agg.get("offerCount"),
        },
        "offers": offers,
        "specs": _specs(tree),
    }
