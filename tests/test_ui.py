"""Renders the MCP Apps page (ui://tpch/app.html) in Chromium inside a fake MCP Apps host.

The fake host answers ui/initialize, sends the tool result, answers tools/call from the page and
records ui/open-link, like claude.ai does. Offline: the CDN library is served from a fixture copy
(whose hash must match the page's integrity pin) and images are replaced by a placeholder.
Set TPCH_UI_SCREENSHOTS=/dir to also save a PNG of every view (TPCH_UI_REAL_IMAGES=1 loads real
product images for a realistic preview).
"""
import base64
import gzip
import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from tpch_mcp.app_ui import app_html
from tpch_mcp.parse.categories import CategoryTree
from tpch_mcp.parse.filters import FilterSet
from tpch_mcp.parse.listing import parse_listing
from tpch_mcp.parse.pricechart import parse_pricechart
from tpch_mcp.parse.product import parse_product
from tpch_mcp.service import Toppreise

FIX = Path(__file__).parent / "fixtures"
LIB = gzip.decompress((FIX / "ext-apps-2.0.4.js.gz").read_bytes())
PNG = base64.b64decode(  # 1x1 light grey pixel
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/58BAwAI/AL+hc2rNAAAAABJRU5ErkJggg==")
SHOTS = os.environ.get("TPCH_UI_SCREENSHOTS")

HOST = """<!doctype html><html><body style="margin:0;padding:12px;background:#faf9f5;font-family:sans-serif">
<iframe id="app" style="width:760px;height:200px;border:0"></iframe>
<script>
window.log = {calls: [], links: [], sizes: []};
const frame = document.getElementById('app');
const send = (msg) => frame.contentWindow.postMessage(msg, '*');
window.addEventListener('message', async (e) => {
  if (e.source !== frame.contentWindow) return;
  const m = e.data;
  if (m.method === 'ui/initialize') {
    send({jsonrpc: '2.0', id: m.id, result: {
      protocolVersion: m.params.protocolVersion,
      hostInfo: {name: 'fake-host', version: '1.0.0'},
      hostCapabilities: {openLinks: {}, serverTools: {}},
      hostContext: {theme: window.THEME, displayMode: 'inline',
                    toolInfo: {tool: {name: window.TOOL, inputSchema: {type: 'object'}}},
                    containerDimensions: {width: 760, maxHeight: 4000}}}});
  } else if (m.method === 'ui/notifications/initialized') {
    send({jsonrpc: '2.0', method: 'ui/notifications/tool-input', params: {arguments: {}}});
    send({jsonrpc: '2.0', method: 'ui/notifications/tool-result',
          params: {content: [{type: 'text', text: JSON.stringify(window.RESULT)}]}});
  } else if (m.method === 'tools/call') {
    window.log.calls.push(m.params);
    const data = window.TOOL_RESPONSES[m.params.name];
    send({jsonrpc: '2.0', id: m.id, result: {content: [{type: 'text', text: JSON.stringify(data)}]}});
  } else if (m.method === 'ui/open-link') {
    window.log.links.push(m.params.url);
    send({jsonrpc: '2.0', id: m.id, result: {}});
  } else if (m.method === 'ui/notifications/size-changed') {
    window.log.sizes.push(m.params);
    frame.style.height = (m.params.height + 4) + 'px';
  } else if (m.id !== undefined) {
    send({jsonrpc: '2.0', id: m.id, result: {}});
  }
});
</script></body></html>"""


def load(name):
    raw = (FIX / name).read_bytes()
    return gzip.decompress(raw).decode() if name.endswith(".gz") else raw.decode()


@pytest.fixture(scope="module")
def data():
    """Tool results built from the HTML fixtures through the real service code."""
    import asyncio

    async def build():
        out = {}
        tp = Toppreise(session=object())
        tp.cache.set("categories", CategoryTree.from_json(load("categorymenu.json.gz")))
        for pid, fx in [(793054, "product_793054.html.gz"), (856700, "product_856700.html.gz")]:
            p = parse_product(load(fx)); p["offers_complete"] = True
            tp.cache.set("product", pid, p)
        out["get_offers"] = await tp.get_offers(793054, limit=8)
        out["get_product"] = await tp.get_product(793054)
        out["compare_products"] = await tp.compare_products([793054, 856700])
        out["get_price_history"] = {"product_id": 793054, **parse_pricechart(load("pricechart_793054.json"), days=365)}

        class Nav:
            def __init__(self, fx): self.fx = fx
            async def navigate(self, path): return load(self.fx)
            async def navigate_and_act(self, path, js, arg, sel): return load(self.fx), False
        tp.b = Nav("toplist_new_top_prices.html.gz")
        out["get_top_list"] = await tp.get_top_list("new_top_prices", "c200", limit=8)
        tp.b = Nav("toplist_top_rated.html.gz")
        out["top_rated"] = await tp.get_top_list("top_rated", limit=8)

        listing = parse_listing(load("listing_mesh_c3543.html.gz"))
        fs = FilterSet(json.loads(load("reload_mesh_expert.json.gz"))["sFilter"])

        async def fake_reload(**kw):
            return FilterSet({**fs.raw, "sSerializedFilters": kw["sfh"]}), listing
        tp._reload = fake_reload
        tp.cache.set("filters", "c3543", True, fs)
        out["browse"] = await tp.browse("c3543", filters={"Wi-Fi standard": ["Wi-Fi 7"]}, sort="price_asc")
        return out

    return asyncio.run(build())


async def render(tool, result, responses=None, theme="light", shot=None):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 800, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.route("https://cdn.jsdelivr.net/**", lambda r: r.fulfill(
            body=LIB, headers={"content-type": "application/javascript", "access-control-allow-origin": "*"}))
        if not os.environ.get("TPCH_UI_REAL_IMAGES"):  # real images only when previewing
            await page.route("https://imgsrv.toppreise.ch/**", lambda r: r.fulfill(body=PNG, content_type="image/png"))
        await page.set_content(HOST)
        await page.evaluate("([t, r, resp, th]) => { window.TOOL = t; window.RESULT = r; window.TOOL_RESPONSES = resp; window.THEME = th; }",
                            [tool, result, responses or {}, theme])
        await page.evaluate("(html) => { document.getElementById('app').srcdoc = html; }", app_html())
        frame = page.frame_locator("#app")
        await frame.locator("#app > *:not(.loading)").first.wait_for(timeout=15000)
        yield_ = {"page": page, "frame": frame, "errors": errors}
        try:
            yield yield_
        finally:
            if shot and SHOTS:
                await page.wait_for_timeout(300)
                await page.locator("#app").screenshot(path=str(Path(SHOTS) / f"{shot}.png"))
            await browser.close()


class Render:
    """`async with Render(...) as r:` wrapper around the render() generator."""
    def __init__(self, *a, **kw): self.gen = render(*a, **kw)
    async def __aenter__(self): return await self.gen.__anext__()
    async def __aexit__(self, *exc):
        try:
            await self.gen.__anext__()
        except StopAsyncIteration:
            pass


def test_integrity_pin_matches_library():
    html = app_html()
    pin = re.search(r'"(sha384-[^"]+)"', html).group(1)
    assert pin == "sha384-" + base64.b64encode(hashlib.sha384(LIB).digest()).decode()
    assert re.search(r'<meta name="referrer" content="no-referrer">', html)


async def test_carousel_with_drop_badges(data):
    d = data["get_top_list"]
    async with Render("get_top_list", d, shot="carousel_new_top_prices") as r:
        cards = r["frame"].locator(".card")
        assert await cards.count() == len(d["items"])
        first = d["items"][0]
        assert f'{first["price_change_pct"]}%' in await cards.first.locator(".badge").inner_text()
        assert await r["frame"].locator("img[referrerpolicy='no-referrer']").count() >= len(d["items"]) - 1
        assert not r["errors"], r["errors"]


async def test_carousel_buttons_call_tools_and_open_links(data):
    d = data["top_rated"]
    responses = {"get_offers": data["get_offers"], "get_price_history": data["get_price_history"]}
    async with Render("get_top_list", d, responses, shot="carousel_top_rated_with_offers") as r:
        frame, page = r["frame"], r["page"]
        assert "★ 6/6" in await frame.locator(".card").first.inner_text()
        await frame.locator(".card").first.get_by_role("button", name="Offers").click()
        await frame.locator(".panel table tbody tr").first.wait_for()
        assert await frame.locator(".panel table tbody tr").count() == len(data["get_offers"]["offers"])
        calls = await page.evaluate("window.log.calls")
        assert calls[-1]["name"] == "get_offers" and calls[-1]["arguments"]["product_id"] == d["items"][0]["id"]
        await frame.locator(".panel").get_by_role("button", name="Shop").first.click()
        await page.wait_for_function("window.log.links.length > 0")
        assert (await page.evaluate("window.log.links"))[0].startswith("https://www.toppreise.ch/")
        assert not r["errors"], r["errors"]


async def test_browse_view(data):
    d = data["browse"]
    async with Render("browse", d, shot="browse_mesh") as r:
        text = await r["frame"].locator("#app").inner_text()
        assert "Mesh systems" in text and "Wi-Fi standard: Wi-Fi 7 (802.11be)" in text
        assert await r["frame"].locator(".card").count() > 20
        assert not r["errors"], r["errors"]


async def test_offers_table(data):
    d = data["get_offers"]
    async with Render("get_offers", d, theme="dark", shot="offers_dark") as r:
        rows = r["frame"].locator("table tbody tr")
        assert await rows.count() == len(d["offers"])
        assert "Foletti Computer" in await rows.first.inner_text()
        assert "★ 5.4/6" in await rows.first.inner_text()
        assert not r["errors"], r["errors"]


async def test_price_history_chart(data):
    d = data["get_price_history"]
    async with Render("get_price_history", d, shot="price_history") as r:
        assert await r["frame"].locator("svg.chart polyline").count() == 1
        assert "Lowest" in await r["frame"].locator(".stats").inner_text()
        assert not r["errors"], r["errors"]


async def test_product_and_compare(data):
    async with Render("get_product", data["get_product"], shot="product") as r:
        assert "PALIT" in await r["frame"].locator("h2").first.inner_text()
        assert not r["errors"], r["errors"]
    d = data["compare_products"]
    async with Render("compare_products", d, shot="compare") as r:
        frame = r["frame"]
        assert await frame.locator("thead th").count() == 3
        rows = frame.locator("tbody tr")
        assert await rows.count() == 2 + min(15, len(d["differences"]))  # best price, offers, 15 specs
        assert "[object" not in await frame.locator("table").inner_text()
        await frame.get_by_role("button", name=f"Show all {len(d['differences'])} differences").click()
        assert await rows.count() == 2 + len(d["differences"])
        assert not r["errors"], r["errors"]


async def test_error_result_is_shown():
    async with Render("get_offers", None) as r:
        assert await r["frame"].locator("#app").inner_text()
