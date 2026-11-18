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
    # Behind Traefik the original Host is forwarded; no allow-list to maintain.
    status, _ = _req(f"{server}/mcp", "POST", INIT, {**MCP_HEADERS, "Host": "tpch.example.ch"})
    assert status == 200
