"""MCP server exposing toppreise.ch (Swiss price comparison) to Claude (mcp SDK 2.x MCPServer)."""
import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .config import SETTINGS
from .keepalive import with_heartbeat
from .service import Toppreise

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_tp: Toppreise | None = None


def tp() -> Toppreise:
    global _tp
    if _tp is None:
        _tp = Toppreise()
    return _tp


@asynccontextmanager
async def lifespan(_server: MCPServer):
    try:
        yield {}
    finally:
        # stdio: one client per process, so close the browser with it. HTTP: the browser (and its
        # warm site session + cache) is shared by all client sessions and lives with the process.
        if _tp is not None and SETTINGS.transport != "http":
            await _tp.close()


INSTRUCTIONS = """\
Prices and offers from toppreise.ch, the Swiss price comparison site (prices in CHF, incl. VAT).

Typical flow:
1. find_category("gaming monitor") -> pick a *leaf* category id (only leaves have spec filters).
2. get_filters(category_id) -> see filter names/values (use names exactly as shown, values as labels).
3. browse(category_id, filters={...}, query="words in the name", price_max=..., sort="price_asc").
   Quick alternative: search("ps5 pro") picks the category for you and filters by name.
4. get_product / get_offers / get_price_history for a product id from the results
   (ids, 'p123456' and full toppreise.ch URLs are all accepted).

toppreise.ch's own site-wide text search is behind a human-verification check, which this server
does not bypass; it always works through categories (+ an in-category name filter) instead.

Shop ratings are on a 1-6 scale (Swiss school grades, 6 = best). Always compare offers by total
price incl. shipping.

The server drives a real browser and paces requests politely, so each uncached call takes a few seconds.
Results are cached (filters 1 h, listings and offers 10 min, price history 6 h).
"""

mcp = MCPServer(
    "tpch",
    title="toppreise.ch - Swiss price comparison",
    website_url="https://www.toppreise.ch",
    version=__version__,
    instructions=INSTRUCTIONS,
    lifespan=lifespan,
)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})



# ------------------------------------------------------------------ tools
@mcp.tool()
async def find_category(
    ctx: Context,
    query: Annotated[str, Field(description="What you are looking for, e.g. 'external ssd', 'mesh wifi', 'graphics card'")],
    limit: Annotated[int, Field(ge=1, le=20)] = 8,
) -> dict:
    """toppreise.ch (Swiss price comparison): find the product category for free text, the first step
    to look up Swiss prices for a type of product (e.g. 'external ssd', 'robot vacuum', 'gaming monitor').
    Returns category ids and paths; prefer leaf categories (is_leaf), which have spec filters.
    Next: get_filters and browse."""
    return await with_heartbeat(ctx, tp().find_category(query, limit))


@mcp.tool()
async def list_categories(
    ctx: Context,
    parent_id: Annotated[str, Field(description="Category id, e.g. 'c200' (Computers & accessories) or 'start' for the top level")] = "c200",
    depth: Annotated[int, Field(ge=0, le=3, description="How many levels below the parent to include")] = 1,
) -> dict:
    """toppreise.ch (Swiss price comparison): browse its product category tree. parent_id 'start' lists the
    top-level departments, 'c200' is Computers & accessories. Use to explore what toppreise covers; for a
    specific type of product, find_category is faster."""
    return await with_heartbeat(ctx, tp().list_categories(parent_id, depth))


@mcp.tool()
async def get_filters(
    ctx: Context,
    category_id: Annotated[str, Field(description="Leaf category id, e.g. 'c37' (graphics cards)")],
    include_expert: Annotated[bool, Field(description="Also list expert filters (often 2-3x more, e.g. dimensions, weight)")] = False,
    filter_name: Annotated[str | None, Field(description="Show all values of one filter (lists are capped otherwise)")] = None,
    value_search: Annotated[str | None, Field(description="With filter_name: only values containing this text")] = None,
) -> dict:
    """toppreise.ch (Swiss price comparison): the filters of a product category (e.g. memory size, Wi-Fi
    standard, brand, price range, release year) with product counts. Use before browse to turn requirements
    into filters; pass filter names and value labels to browse exactly as returned."""
    return await with_heartbeat(ctx, tp().get_filters(category_id, include_expert, filter_name, value_search))


@mcp.tool()
async def browse(
    ctx: Context,
    category_id: Annotated[str, Field(description="Category id from find_category / search, e.g. 'c2883' (PS5 consoles)")],
    query: Annotated[str | None, Field(description="Optional words the product name must contain, e.g. 'pro' or 'rtx 5090'")] = None,
    filters: Annotated[dict[str, Any] | None, Field(description=(
        "Filter name -> wanted value(s), names/labels as returned by get_filters. "
        "'any of'/'all of': list of labels, e.g. {\"chipset\": [\"RTX 5080\", \"RTX 5090\"]}. "
        "'yes/no': true. 'at least'/'at most'/'from-to': {\"min\": \"16 GB\"}, {\"max\": \"300 W\"}, "
        "{\"min\": \"1 TB\", \"max\": \"2 TB\"}. Thresholds snap to the nearest site value that does not "
        "loosen your limit."))] = None,
    brands: Annotated[list[str] | None, Field(description="Brand names, e.g. ['Samsung', 'WD']")] = None,
    price_min: Annotated[float | None, Field(description="CHF")] = None,
    price_max: Annotated[float | None, Field(description="CHF")] = None,
    availability: Annotated[Literal["in_stock", "external_warehouse", "within_week", "within_two_weeks", "any"] | None,
                            Field(description="Worst acceptable delivery availability")] = None,
    min_year: Annotated[int | None, Field(description="Released in or after this year")] = None,
    sort: Annotated[Literal["price_asc", "price_desc", "popular", "newest", "most_offers", "availability",
                            "name_asc", "name_desc"] | None, Field(description="Default: site relevance")] = None,
    page: Annotated[int, Field(ge=1)] = 1,
) -> dict:
    """toppreise.ch (Swiss price comparison): list the products of a category with filters, e.g. sorted by
    lowest price; the main tool for "cheapest X with Y in Switzerland". Prices in CHF incl. VAT: 'from'
    prices with and without shipping, offer count, price per unit. Returns the filters the site actually
    applied; use the product ids with get_offers or get_price_history."""
    return await with_heartbeat(ctx, tp().browse(category_id, query, filters, brands, price_min, price_max, availability,
                             min_year, sort, page))


@mcp.tool()
async def search(
    ctx: Context,
    query: Annotated[str, Field(description="Product words, e.g. 'ps5', 'rtx 5090', 'samsung 990 pro'")],
    page: Annotated[int, Field(ge=1)] = 1,
    sort: Annotated[Literal["price_asc", "price_desc", "popular", "newest", "most_offers", "availability",
                            "name_asc", "name_desc"] | None, Field(description="e.g. price_asc for the cheapest")] = None,
) -> dict:
    """toppreise.ch (Swiss price comparison): quick lookup of Swiss prices (CHF) for a product name or type,
    e.g. 'ps5', 'rtx 5090', 'samsung 990 pro'. Picks the best category and lists matching products with
    'from' prices and offer counts. Use for "how much does X cost in Switzerland"; for precise
    requirements continue with get_filters and browse."""
    return await with_heartbeat(ctx, tp().search(query, page, sort))


@mcp.tool()
async def get_top_list(
    ctx: Context,
    list: Annotated[Literal["top100", "top_rated", "new_top_prices", "new_products"], Field(description=(
        "top100 = most popular products, top_rated = best user ratings, "
        "new_top_prices = biggest recent price drops, new_products = newly listed products"))] = "top100",
    category_id: Annotated[str | None, Field(description="Optional category id (any level, e.g. 'c200' Computers & accessories)")] = None,
    hours: Annotated[Literal[1, 2, 4, 8, 12, 24, 48], Field(description="new_top_prices only: price drops within the last N hours (48 = longest)")] = 48,
    page: Annotated[int, Field(ge=1)] = 1,
    limit: Annotated[int, Field(ge=1, le=96, description="Max items returned (the site shows up to 96 per page)")] = 24,
) -> dict:
    """toppreise.ch (Swiss price comparison): its curated lists - top100 (most popular), top_rated (best
    user ratings, out of 6), new_top_prices (biggest price drops in the last 1-48 h, i.e. current deals)
    and new_products - site-wide or for a category. Use for deals, bestsellers or what's new in
    Switzerland. Prices in CHF."""
    return await with_heartbeat(ctx, tp().get_top_list(list, category_id, hours, page, limit))


@mcp.tool()
async def get_product(
    ctx: Context,
    product_id: Annotated[int | str, Field(description="Product id: 779730, 'p779730' or a full toppreise.ch product URL")],
) -> dict:
    """toppreise.ch (Swiss price comparison): details of one product - brand, MPN/EAN, category, full data
    sheet (specs), Swiss price range in CHF, number of shops and the cheapest offer. Accepts an id,
    'p123456' or a toppreise.ch URL."""
    return await with_heartbeat(ctx, tp().get_product(product_id))


@mcp.tool()
async def get_offers(
    ctx: Context,
    product_id: Annotated[int | str, Field(description="Product id: 779730, 'p779730' or a full toppreise.ch product URL")],
    sort: Literal["total", "price", "availability", "rating"] = "total",
    in_stock_only: bool = False,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict:
    """toppreise.ch (Swiss price comparison): where to buy a product in Switzerland - every shop's offer with
    price, shipping and total in CHF, availability, shop rating and link. Compare by 'total' (incl.
    shipping; it can include shop discounts, so it is occasionally below 'price'). Shop ratings are out
    of 6 (6 = best), not out of 5."""
    return await with_heartbeat(ctx, tp().get_offers(product_id, sort, in_stock_only, limit))


@mcp.tool()
async def get_price_history(
    ctx: Context,
    product_id: Annotated[int | str, Field(description="Product id: 779730, 'p779730' or a full toppreise.ch product URL")],
    days: Annotated[int, Field(ge=7, le=365)] = 90,
) -> dict:
    """toppreise.ch (Swiss price comparison): history of a product's lowest Swiss price (CHF incl. shipping)
    - current, lowest and when, highest, average, change over 7/30/90 days, and whether it is at its
    lowest now. Use for "is this a good deal" or "buy now or wait"."""
    return await with_heartbeat(ctx, tp().get_price_history(product_id, days))


@mcp.tool()
async def compare_products(
    ctx: Context,
    product_ids: Annotated[list[int | str], Field(min_length=2, max_length=5,
                                                  description="Product ids or toppreise.ch URLs")],
) -> dict:
    """toppreise.ch (Swiss price comparison): compare 2-5 products side by side - best Swiss price (CHF incl.
    shipping), number of shops and the specs that differ. Accepts ids or toppreise.ch URLs."""
    return await with_heartbeat(ctx, tp().compare_products(product_ids))


# ------------------------------------------------------------------ resources
@mcp.resource("toppreise://categories", mime_type="application/json")
async def categories_resource() -> str:
    """Full category tree (ids, names, parents)."""
    tree = await tp().categories()
    return json.dumps(tree.subtree("start", 9), ensure_ascii=False)


@mcp.resource("toppreise://categories/{category_id}/filters", mime_type="application/json")
async def filters_resource(category_id: str) -> str:
    """All filters of a category, including expert filters."""
    return json.dumps(await tp().get_filters(category_id, include_expert=True), ensure_ascii=False)


@mcp.resource("toppreise://products/{product_id}", mime_type="application/json")
async def product_resource(product_id: str) -> str:
    """Product details (same as get_product)."""
    return json.dumps(await tp().get_product(int(product_id)), ensure_ascii=False)


# ------------------------------------------------------------------ prompts
@mcp.prompt()
def best_deal(product: str, budget_chf: str = "", must_have: str = "") -> str:
    """Find the best offer for a product description within a budget."""
    return (
        f"Find me the best deal on toppreise.ch for: {product}."
        + (f" Budget: up to CHF {budget_chf}." if budget_chf else "")
        + (f" Must have: {must_have}." if must_have else "")
        + " Use find_category to pick the right leaf category, get_filters to translate my requirements"
          " into filters, browse sorted by price, then get_offers for the top 2-3 candidates."
          " Prefer in-stock offers from well-rated shops; show total price incl. shipping and links."
    )


@mcp.prompt()
def buy_now_or_wait(product_id: str) -> str:
    """Decide whether now is a good time to buy a product."""
    return (
        f"For toppreise product {product_id}: call get_price_history (365 days) and get_offers."
        " Tell me whether the current best total price is a good deal compared with its history"
        " (lowest, average, recent trend) and recommend buying now or waiting, with reasons."
    )


@mcp.prompt()
def compare(products: str) -> str:
    """Compare products by name or id."""
    return (
        f"Compare these products on toppreise.ch: {products}. Resolve names to product ids with"
        " search/browse if needed, then use compare_products and summarise the meaningful differences"
        " and the best price for each."
    )


def main() -> None:
    if SETTINGS.transport == "stdio":
        mcp.run()
        return
    if SETTINGS.transport != "http":
        sys.exit(f"MCP_TRANSPORT must be 'stdio' or 'http', not {SETTINGS.transport!r}")
    logging.getLogger(__name__).warning(
        "HTTP transport has no built-in auth: keep it on a private network or behind an "
        "authenticating reverse proxy.")
    mcp.run(
        transport="streamable-http",
        host=SETTINGS.http_host,
        port=SETTINGS.http_port,
        streamable_http_path=SETTINGS.http_path,
        # The Host-header check guards unauthenticated servers on localhost against DNS rebinding;
        # here the reverse proxy's secret-header check is the access control, so it is off.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

if __name__ == "__main__":
    main()
