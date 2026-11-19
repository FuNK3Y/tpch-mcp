"""Remote transport (Streamable HTTP). Auth is done by the reverse proxy. No network needed."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

INIT = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}).encode()
MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _req(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


@pytest.fixture
def server():
    port = _free_port()
    base = f"http://localhost:{port}"
    env = {**os.environ, "MCP_TRANSPORT": "http", "MCP_HTTP_HOST": "127.0.0.1", "MCP_HTTP_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, "-m", "tpch_mcp"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(100):
        try:
            if _req(f"{base}/healthz")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail(proc.stderr.read().decode()[-2000:])
    yield base
    proc.terminate()
    proc.wait(timeout=10)


def test_mcp_endpoint(server):
    status, body = _req(f"{server}/mcp", "POST", INIT, MCP_HEADERS)
    assert status == 200 and '"name":"tpch"' in body.replace(" ", "")


@pytest.mark.parametrize("path", ["/", "/wrong/mcp"])
def test_other_paths_are_404(server, path):
    assert _req(f"{server}{path}", "POST", INIT, MCP_HEADERS)[0] == 404


def test_proxied_host_header_accepted(server):
    # Behind a reverse proxy the original Host is forwarded; no allow-list to maintain.
    status, _ = _req(f"{server}/mcp", "POST", INIT, {**MCP_HEADERS, "Host": "tpch.example.ch"})
    assert status == 200


def test_healthcheck_command(server):
    port = server.rsplit(":", 1)[1]
    env = {**os.environ, "MCP_TRANSPORT": "http", "MCP_HTTP_PORT": port}
    ok = subprocess.run([sys.executable, "-m", "tpch_mcp.healthcheck"], env=env, timeout=30)
    assert ok.returncode == 0


def test_healthcheck_command_unhealthy_and_stdio():
    env = {**os.environ, "MCP_TRANSPORT": "http", "MCP_HTTP_PORT": str(_free_port())}  # nothing listening
    assert subprocess.run([sys.executable, "-m", "tpch_mcp.healthcheck"], env=env, timeout=30).returncode == 1
    env = {**os.environ, "MCP_TRANSPORT": "stdio"}
    assert subprocess.run([sys.executable, "-m", "tpch_mcp.healthcheck"], env=env, timeout=30).returncode == 0


SLOW_SERVER = """
import asyncio, sys
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.transport_security import TransportSecuritySettings
from tpch_mcp.keepalive import with_heartbeat
mcp = MCPServer("slow")

@mcp.tool()
async def slow_with_heartbeat(ctx: Context) -> dict:
    return await with_heartbeat(ctx, asyncio.sleep(7, result={"ok": True}))

@mcp.tool()
async def slow_silent() -> dict:
    return await asyncio.sleep(7, result={"ok": True})

mcp.run(transport="streamable-http", host="127.0.0.1", port=int(sys.argv[1]),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
"""


async def test_heartbeat_keeps_slow_calls_alive_for_strict_clients():
    """Clients with httpx's default 5 s read timeout (e.g. Home Assistant's MCP client) drop
    silent responses; the heartbeat keeps 7 s calls alive."""
    import asyncio
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-c", SLOW_SERVER, str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

        async def call(tool):
            async with asyncio.timeout(30):  # Home Assistant's hard cap per tool call
                async with httpx2.AsyncClient() as http, \
                        streamable_http_client(url=f"http://127.0.0.1:{port}/mcp", http_client=http) as (r, w), \
                        ClientSession(r, w) as s:
                    await s.initialize()
                    return await s.call_tool(tool, {})

        ok = await call("slow_with_heartbeat")
        assert not ok.is_error and '"ok": true' in ok.content[0].text.replace(" ", " ")
        with pytest.raises(BaseException):  # the failure mode we fixed: silent > 5 s
            await call("slow_silent")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
