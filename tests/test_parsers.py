import json

import pytest

from tpch_mcp.parse.autocomplete import parse_autocomplete
from tpch_mcp.parse.categories import CategoryTree
from tpch_mcp.parse.listing import parse_listing
from tpch_mcp.parse.pricechart import parse_pricechart
from tpch_mcp.parse.product import parse_product


@pytest.mark.parametrize("name,offers,count,lowest,specs", [
    ("product_793054.html.gz", 15, 15, 7074.45, 34),
    ("product_856700.html.gz", 11, 11, 1899.0, 46),
    ("product_827702.html.gz", 6, 6, 950.0, 6),     # page also lists offers of a similar product
    ("product_747429.html.gz", 24, 28, 169.0, 36),  # offer list paginated at 24 by the site
])
def test_product(fx, name, offers, count, lowest, specs):
    p = parse_product(fx(name))
    assert len(p["offers"]) == offers
    assert p["price"]["offer_count"] == count
    assert p["price"]["min"] == lowest
    assert min(o["total"] for o in p["offers"]) == lowest
    # every data-sheet row except empty "-" values
    from selectolax.lexbor import LexborHTMLParser
    feats = LexborHTMLParser(fx(name)).css("div.Plugin_ProductNgfFeature")
    assert len(feats) == specs
    non_empty = [f for f in feats if f.css_first(".value").text(strip=True) not in ("", "-")]
    assert sum(len(v) for v in p["specs"].values()) == len(non_empty)
    assert p["category_id"] and p["category_id"].startswith("c")
    for o in p["offers"]:
        assert o["shop"] and o["price"] is not None and o["total"] is not None and o["url"]
        assert o["availability"]["level"] in (1, 2, 3, 4, 7, 8, 9)


def test_product_details(fx):
    p = parse_product(fx("product_793054.html.gz"))
    assert p["id"] == 793054 and p["brand"] == "PALIT" and p["gtin"] == "4710562244847"
    assert p["category_id"] == "c37"
    first = min(p["offers"], key=lambda o: o["total"])
    assert first["shop"] == "Foletti Computer" and first["shop_rating"] == 5.4 and first["shop_rating_count"] == 2797
    assert p["specs"]["Memory"]["memory size"] == "32 GB"


def test_listing_series_and_products(fx):
    r = parse_listing(fx("listing_mesh_c3543.html.gz"))
    assert len(r["items"]) == 96 and (r["page"], r["pages"]) == (1, 2)
    series = [i for i in r["items"] if i["type"] == "series"]
    assert len(series) == 34 and all(s["variants"] for s in series)
    rows = [i for i in r["items"] if i["type"] == "product"] + [v for s in series for v in s["variants"]]
    assert all(x["name"] and x["price"] and x["offer_count"] and x["url"] for x in rows)


def test_search_has_uncategorised_offers(fx):
    r = parse_listing(fx("search_rtx5090.html.gz"))
    kinds = [i["type"] for i in r["items"]]
    assert kinds.count("offer") == 65 and len(kinds) == 96
    o = next(i for i in r["items"] if i["type"] == "offer")
    assert o["shop"] and o["total"] and o["url"]


@pytest.mark.parametrize("name,offer_count_present", [("reload_table_view.json.gz", False),
                                                      ("reload_grid_view.json.gz", True)])
def test_listing_views(fxj, name, offer_count_present):
    r = parse_listing(fxj(name)["sContent"]["sHTML"])
    assert len(r["items"]) == 96
    first = r["items"][0]
    assert first["name"].startswith("TRANSCEND") and first["price_per_unit"]["unit"] == "TB"
    assert (first["offer_count"] is not None) == offer_count_present


def test_categories(fx):
    t = CategoryTree.from_json(fx("categorymenu.json.gz"))
    assert len(t.nodes) == 3042
    assert t.path("c37") == ["Computers & accessories", "PC components", "Graphics cards & accessories", "Graphics cards"]
    assert len(t.nodes["c200"].children) == 14
    hits = t.search("external ssd")
    assert hits[0]["id"] == "c2417" and hits[0]["is_leaf"]
    assert t.subtree("c200", 0)["subcategories"] == 14


def test_pricechart(fx):
    r = parse_pricechart(fx("pricechart_793054.json"), days=365)
    assert r["current"] == 7102.2 and r["points"] > 300
    assert r["lowest"]["price"] <= r["average"] <= r["highest"]["price"]
    assert len(r["series"]) <= 26
    assert parse_pricechart("[]")["points"] == 0


def test_autocomplete(fx):
    r = parse_autocomplete(fx("autocomplete_rtx5090.json"))
    ids = [c["category_id"] for c in r["categories"]]
    assert "c37" in ids and "rtx 5090 ti" in r["suggested_queries"]


def test_pricechart_window_change(fx):
    r = parse_pricechart(fx("pricechart_793054.json"), days=90)
    assert r["points"] == 90 and r["change_pct"]["90d"] is not None


@pytest.mark.parametrize("fixture,kind,n,extra", [
    ("toplist_top100.html.gz", "top100", 96, None),
    ("toplist_top_rated.html.gz", "top_rated", 96, "rating"),
    ("toplist_new_top_prices.html.gz", "new_top_prices", 96, "price_change_pct"),
    ("toplist_new_products.html.gz", "new_products", 96, None),
    ("toplist_new_top_prices_c3836.html.gz", "new_top_prices", 1, "price_change_pct"),
    ("toplist_top100_c200.html.gz", "top100", 96, None),
])
def test_toplists(fx, fixture, kind, n, extra):
    from tpch_mcp.parse.toplist import parse_toplist
    r = parse_toplist(fx(fixture), kind)
    assert len(r["items"]) == n and [i["rank"] for i in r["items"]] == list(range(1, n + 1))
    for i in r["items"]:
        assert i["id"] and i["name"] and i["price"] and i["price_incl_shipping"] and i["url"]
        assert i["image"] is None or "/img/" in i["image"] and "/400-" in i["image"]
        if extra:
            assert extra in i
    assert sum(1 for i in r["items"] if i["image"]) >= int(n * 0.9)  # a few products have no photo
    if kind == "new_top_prices":
        assert r["period_hours"] == 48
        assert all(i["price_change_pct"] < 0 for i in r["items"])
    if kind == "top_rated":
        assert r["items"][0]["rating"] == "6/6" and r["items"][0]["ratings_count"] == 5
