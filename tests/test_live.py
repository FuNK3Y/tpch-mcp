"""End-to-end against the real toppreise.ch. Slow and polite; run explicitly:

    docker run --rm --init tpch-mcp pytest -q -m live -s
"""
import json

import pytest

from tpch_mcp.service import Toppreise

pytestmark = pytest.mark.live


def show(label, data):
    print(f"\n=== {label}\n{json.dumps(data, ensure_ascii=False)[:1500]}")


async def test_end_to_end():
    tp = Toppreise()
    try:
        cats = await tp.find_category("external ssd")
        show("find_category", cats)
        assert any(c["id"] == "c2417" for c in cats["categories"])

        f = await tp.get_filters("c2417")
        show("get_filters", f)
        assert f["matches"] > 100 and any(x["name"] == "total capacity" for x in f["filters"])

        res = await tp.browse("c2417", filters={"total capacity": {"min": "2 TB", "max": "2 TB"}},
                              brands=["Samsung", "SanDisk"], availability="in_stock", sort="price_asc")
        show("browse", res)
        assert res["matches"] > 0 and res["items"] and "warnings" not in res, res.get("warnings")

        page2 = await tp.browse("c2417", sort="price_asc", page=2)
        assert page2["page"] == 2, (page2["page"], page2["pages"])

        first = next(i for i in res["items"] if i["type"] == "product") if any(
            i["type"] == "product" for i in res["items"]) else res["items"][0]["variants"][0]
        pid = first["id"]
        tp.product_paths.clear()  # force the id-only URL path
        prod = await tp.get_product(pid)
        show("get_product", prod)
        assert prod["id"] == pid and prod["specs"] and prod["best_offer"]

        offers = await tp.get_offers(pid, in_stock_only=False, limit=50)
        show("get_offers", {k: v for k, v in offers.items() if k != "offers"} | {"first": offers["offers"][:2]})
        assert offers["offers"] and "warning" not in offers

        hist = await tp.get_price_history(pid, days=90)
        show("get_price_history", {k: v for k, v in hist.items() if k != "series"})
        assert hist["points"] > 0

        big = await tp.get_offers(747429, limit=100)  # 28 offers, site shows 24 per page by default
        show("get_offers 747429", {k: v for k, v in big.items() if k != "offers"})
        assert big["shown"] >= 25 and "warning" not in big
    finally:
        await tp.close()
