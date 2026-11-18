"""MCP Apps page (ui://tpch/app.html) rendering results in hosts like claude.ai.

The page is a single HTML file (ui/app.html). It loads the official MCP Apps browser library
(@modelcontextprotocol/ext-apps, pinned version + integrity hash) from jsDelivr, and product
images from toppreise's image server, both without a Referer. Those two origins are the only
external resources the page may load.
"""
from importlib.resources import files

UI_URI = "ui://tpch/app.html"
UI_RESOURCE_DOMAINS = ["https://imgsrv.toppreise.ch", "https://cdn.jsdelivr.net"]


def app_html() -> str:
    return files("tpch_mcp").joinpath("ui/app.html").read_text(encoding="utf-8")
