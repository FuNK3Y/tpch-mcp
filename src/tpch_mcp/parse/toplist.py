"""Home-page top lists (server-rendered pages):

    /top-products[/<category path>]          "Top 100"         popular products
    /best-rated-products[/<category path>]   "Top ratings"     user ratings (1-6)
    /new-best-prices[/<category path>]       "New top prices"  price drops in the last 1..48 h
    /new-products[/<category path>]          "New products"

Cards are <a class="Plugin_Product medium-box" data-entity-id=...> inside a *ListFull container.
"""
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .util import absolute, price, text

LISTS = {
    "top100": ("/top-products", "Plugin_TopProductsListFull", "Top 100 (most popular)"),
    "top_rated": ("/best-rated-products", "Plugin_BestRatedProductsListFull", "Top ratings"),
    "new_top_prices": ("/new-best-prices", "Plugin_TopPriceReductionProductListFull", "New top prices (price drops)"),
    "new_products": ("/new-products", "Plugin_NewProductsListFull", "New products"),
}
PERIODS_H = (1, 2, 4, 8, 12, 24, 48)


def _image(card: LexborNode, size: int = 400) -> str | None:
    img = card.css_first("img.Plugin_Image")
    if img is None:
        return None
    src = img.attributes.get("data-src") or img.attributes.get("src") or ""  # lazy images keep it in data-src
    if not src or src.endswith(".svg") or "/img/" not in src:  # lazy placeholder / "no photo" image
        return None
    src = re.sub(r"/(\d+)-([0-9a-f]+)$", rf"/{size}-\2", src)
    return "https:" + src if src.startswith("//") else src


def _card(card: LexborNode, rank: int) -> dict:
    a = card.attributes
    href = a.get("href") or ""
    rating = rcount = None
    rt = card.css_first(".f_ratingStarsText")
    if rt is not None:
        rating = price(text(rt))
    rc = card.css_first(".ratingsCount")
    if rc is not None:
        m = re.search(r"(\d[\d']*)", text(rc) or "")
        rcount = int(m.group(1).replace("'", "")) if m else None
    drop = None
    badge = card.css_first(".badge-dif")
    if badge is not None:
        m = re.search(r"(-?\d+)\s*%", text(badge) or "")
        drop = int(m.group(1)) if m else None
    item = {
        "rank": rank,
        "id": int(a["data-entity-id"]) if a.get("data-entity-id") else None,
        "name": text(card.css_first(".product-name")),
        "price": price(text(card.css_first(".productPrice .Plugin_Price"))),
        "price_incl_shipping": price(text(card.css_first(".shippingPrice .Plugin_Price"))),
        "image": _image(card),
        "url": absolute(href.split("?")[0]) if href else None,
    }
    if rating:
        item["rating"] = f"{rating:g}/6"
        item["ratings_count"] = rcount
    if drop is not None:
        item["price_change_pct"] = drop
    return item


def parse_toplist(html: str, kind: str) -> dict:
    if kind not in LISTS:
        raise ValueError(f"unknown list {kind!r}; one of {list(LISTS)}")
    _, container_cls, label = LISTS[kind]
    tree = LexborHTMLParser(html)
    cont = tree.css_first(f"div.{container_cls}") or tree.css_first('[class*="ListFull"]')
    cards = cont.css(".Plugin_Product.medium-box") if cont is not None else []
    items = [_card(c, i + 1) for i, c in enumerate(cards) if c.attributes.get("data-entity-id")]

    page = pages = 1
    sel = tree.css_first('select[aria-label^="Page "]')
    if sel is not None:
        m = re.search(r"Page (\d+) of (\d+)", sel.attributes.get("aria-label") or "")
        if m:
            page, pages = int(m.group(1)), int(m.group(2))
    res = {"list": kind, "title": label, "page": page, "pages": pages, "items": items}
    if kind == "new_top_prices":
        tp = tree.css_first(".Plugin_TimePeriod input.f_timePeriod[checked]")
        res["period_hours"] = int(tp.attributes["value"]) if tp is not None else None
    return res
