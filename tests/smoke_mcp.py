"""MCP smoke test (no network): start the server over stdio with the official client and check
what it exposes. Used by CI:  docker run --rm tpch-mcp python tests/smoke_mcp.py"""
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {"find_category", "list_categories", "get_filters", "browse", "search", "get_top_list",
                  "get_product", "get_offers", "get_price_history", "compare_products"}
EXPECTED_PROMPTS = {"best_deal", "buy_now_or_wait", "compare"}


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "tpch_mcp"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        init = await s.initialize()
        tools = {t.name for t in (await s.list_tools()).tools}
        prompts = {p.name for p in (await s.list_prompts()).prompts}
        templates = {t.uri_template for t in (await s.list_resource_templates()).resource_templates}
        print(f"server={init.server_info.name} ({init.server_info.title}) v{init.server_info.version} protocol={init.protocol_version}")
        print(f"tools={sorted(tools)}\nprompts={sorted(prompts)}\ntemplates={sorted(templates)}")
        missing = (EXPECTED_TOOLS - tools) | (EXPECTED_PROMPTS - prompts)
        if init.server_info.name != "tpch" or "toppreise" not in (init.server_info.title or "") or missing or "toppreise://products/{product_id}" not in templates:
            print(f"FAIL: missing={sorted(missing)}")
            return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
