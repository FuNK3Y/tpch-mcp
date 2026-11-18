import pytest

from tpch_mcp.parse.product import parse_product
from tpch_mcp.service import Toppreise, product_id_of, rating_text


@pytest.mark.parametrize("value,expected", [
    (779730, 779730),
    ("779730", 779730),
    ("p779730", 779730),
    (" P779730 ", 779730),
    ("https://www.toppreise.ch/price-comparison/Smartphones/APPLE-iPhone-16-256GB-Black-MYEE3QL-A-p779730", 779730),
    ("https://www.toppreise.ch/price-comparison/Graphics-cards/PALIT-GeForce-RTX-5090-GameRock-OC-GeForce-"
     "NE75090S19R5-GB2020G-p793054?selsort=va#prices", 793054),
    ("/price-comparison/-p747429", 747429),
])
def test_product_id_of(value, expected):
    assert product_id_of(value) == expected


@pytest.mark.parametrize("bad", ["", "abc", "c37", "https://www.toppreise.ch/browse/Graphics-cards-c37"])
def test_product_id_of_rejects(bad):
    with pytest.raises(ValueError, match="product id"):
        product_id_of(bad)


def test_rating_text():
    assert rating_text(5.4) == "5.4/6" and rating_text(6.0) == "6/6" and rating_text(None) is None


async def test_get_offers_formats_ratings_and_accepts_urls(fx):
    tp = Toppreise(session=object())  # no browser: product comes from the cache
    data = parse_product(fx("product_793054.html.gz"))
    data["offers_complete"] = True
    tp.cache.set("product", 793054, data)
    res = await tp.get_offers("https://www.toppreise.ch/price-comparison/x/PALIT-RTX-5090-p793054", limit=3)
    assert res["offers"][0]["shop"] == "Foletti Computer"
    assert res["offers"][0]["shop_rating"] == "5.4/6"
    assert "out of 6" in res["note"]
    by_rating = await tp.get_offers("p793054", sort="rating", limit=15)
    assert by_rating["offers"][0]["shop_rating"] == max(
        (o["shop_rating"] for o in by_rating["offers"] if o["shop_rating"]), key=lambda r: float(r.split("/")[0]))


# ---------------------------------------------------------------- search / browse without "q"
from tpch_mcp import sfh as sfhmod
from tpch_mcp.browser import looks_challenged
from tpch_mcp.parse.categories import CategoryTree
from tpch_mcp.parse.filters import FilterSet


def test_challenge_page_detected():
    page = ('<html><head><title>Quick check</title></head><body><form class="f_eanCheck" '
            'data-turnstile-site-key="0x4AAA" data-altcha-challenge="{}" data-kind="search"></form></body></html>')
    assert looks_challenged(page)
    assert not looks_challenged("<html><head><title>rtx 5090 - Price comparison</title></head></html>")


class _FakeReload:
    """Stands in for Toppreise._reload; records what would be sent."""

    def __init__(self, matches_by_text):
        self.calls = []
        self.matches_by_text = matches_by_text

    async def __call__(self, *, category_id, sfh, query="", content, expert=False):
        self.calls.append({"category_id": category_id, "sfh": sfh, "query": query})
        text = sfhmod.decode(sfh).text
        n = self.matches_by_text.get(text, 0)
        listing = {"page": 1, "pages": 1, "page_size": 24, "items": [
            {"type": "product", "id": 1000 + i, "name": f"PS5 item {i}", "url": f"https://x/-p{1000 + i}"}
            for i in range(min(n, 3))]}
        return FilterSet({"aFilter": [], "iMatchingsCount": n, "sSerializedFilters": sfh}), listing


def _tp_with_tree(fx, fake):
    tp = Toppreise(session=object())
    tp.cache.set("categories", CategoryTree.from_json(fx("categorymenu.json.gz")))
    tp._reload = fake
    return tp


async def test_browse_query_is_text_filter_not_q(fx):
    fake = _FakeReload({"pro": 3})
    tp = _tp_with_tree(fx, fake)
    res = await tp.browse("c2883", query="pro", sort="price_asc")
    call = fake.calls[-1]
    assert call["query"] == ""                       # never a plain search query
    assert sfhmod.decode(call["sfh"]).text == "pro"  # sent as text filter
    assert res["matches"] == 3 and 'name contains "pro"' in res["applied"] and "warnings" not in res
    with pytest.raises(ValueError, match="category_id"):
        await tp.browse(None, query="ps5")


async def test_search_routes_through_category(fx):
    fake = _FakeReload({"ps5": 13})
    tp = _tp_with_tree(fx, fake)

    async def ac(q):
        return {"categories": [{"category_id": "c2882"}, {"category_id": "c2883"}], "suggested_queries": []}
    tp.autocomplete = ac
    res = await tp.search("ps5", sort="price_asc")
    assert res["category"]["id"] == "c2883"          # first *leaf* suggestion wins over the parent c2882
    assert res["matches"] == 13 and res["other_categories"]
    assert all(c["query"] == "" for c in fake.calls)


async def test_search_falls_back_to_whole_category(fx):
    fake = _FakeReload({None: 13})                   # nothing matches the text filter
    tp = _tp_with_tree(fx, fake)

    async def ac(q):
        return {"categories": [{"category_id": "c2883"}], "suggested_queries": []}
    tp.autocomplete = ac
    res = await tp.search("cheapest playstation")
    assert res["matches"] == 13 and any("showing the whole category" in w for w in res["warnings"])


class _FakeNav:
    def __init__(self, fx):
        self.fx, self.paths = fx, []

    async def navigate(self, path):
        self.paths.append(path)
        return self.fx("toplist_top100_c200.html.gz")

    async def navigate_and_act(self, path, js, arg, sel):
        self.paths.append(path)
        return self.fx("toplist_new_top_prices.html.gz"), False


async def test_top_list_paths(fx):
    nav = _FakeNav(fx)
    tp = Toppreise(session=nav)
    tp.cache.set("categories", CategoryTree.from_json(fx("categorymenu.json.gz")))
    res = await tp.get_top_list("top100", "c200", limit=5)
    assert nav.paths[-1] == "/top-products/Computers-accessories-c200"
    assert len(res["items"]) == 5 and res["count_on_page"] == 96 and res["category"]["id"] == "c200"
    await tp.get_top_list("new_products", "c3836", page=2)
    assert nav.paths[-1] == ("/new-products/Computers-accessories/Single-board-computers-maker/"
                             "Cases-for-single-board-computers-c3836?p=1")
    r = await tp.get_top_list("new_top_prices")
    assert nav.paths[-1] == "/new-best-prices" and "note" in r
    with pytest.raises(ValueError):
        await tp.get_top_list("new_top_prices", hours=3)
