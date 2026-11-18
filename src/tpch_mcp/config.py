"""Runtime settings from environment variables: TPCH_* for this app, MCP_* for the transport."""
import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    base_url: str = os.environ.get("TPCH_BASE_URL", "https://www.toppreise.ch")
    lang: str = os.environ.get("TPCH_LANG", "en")
    # Pause between full page loads (seconds, random within range).
    nav_delay_min: float = _float("TPCH_NAV_DELAY_MIN", 5.0)
    nav_delay_max: float = _float("TPCH_NAV_DELAY_MAX", 10.0)
    # Pause between in-page XHR calls.
    xhr_delay_min: float = _float("TPCH_XHR_DELAY_MIN", 2.0)
    xhr_delay_max: float = _float("TPCH_XHR_DELAY_MAX", 4.0)
    # Short "reading" pause after a page has loaded (after a small scroll).
    settle_min: float = _float("TPCH_SETTLE_MIN", 0.3)
    settle_max: float = _float("TPCH_SETTLE_MAX", 0.7)
    headless: bool = os.environ.get("TPCH_HEADLESS", "0") == "1"
    timeout_ms: int = int(_float("TPCH_TIMEOUT_MS", 45000))
    # Known plugin context hashes (stable across sessions).
    categorymenu_hash: str = os.environ.get("TPCH_CATEGORYMENU_HASH", "849247345")
    pricechart_hash: str = os.environ.get("TPCH_PRICECHART_HASH", "1329180698")

    # ---- transport (generic MCP server settings, hence the MCP_ prefix) ----
    # "stdio" (Claude Code / Desktop) or "http" (remote, e.g. claude.ai)
    transport: str = os.environ.get("MCP_TRANSPORT", "stdio")
    http_host: str = os.environ.get("MCP_HTTP_HOST", "0.0.0.0")
    http_port: int = int(_float("MCP_HTTP_PORT", 8000))
    # The HTTP transport has no built-in auth: run it behind a reverse proxy that checks a secret
    # header (see README: Traefik `Header(...)` router rule).
    http_path: str = os.environ.get("MCP_HTTP_PATH", "/mcp")


SETTINGS = Settings()
