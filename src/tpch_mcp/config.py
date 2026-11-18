"""Runtime settings, overridable with environment variables."""
import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    base_url: str = os.environ.get("TOPPREISE_BASE_URL", "https://www.toppreise.ch")
    lang: str = os.environ.get("TOPPREISE_LANG", "en")
    # Pause between full page loads (seconds, random within range).
    nav_delay_min: float = _float("TOPPREISE_NAV_DELAY_MIN", 5.0)
    nav_delay_max: float = _float("TOPPREISE_NAV_DELAY_MAX", 10.0)
    # Pause between in-page XHR calls.
    xhr_delay_min: float = _float("TOPPREISE_XHR_DELAY_MIN", 2.0)
    xhr_delay_max: float = _float("TOPPREISE_XHR_DELAY_MAX", 4.0)
    # Short "reading" pause after a page has loaded (after a small scroll).
    settle_min: float = _float("TOPPREISE_SETTLE_MIN", 0.3)
    settle_max: float = _float("TOPPREISE_SETTLE_MAX", 0.7)
    headless: bool = os.environ.get("TOPPREISE_HEADLESS", "0") == "1"
    timeout_ms: int = int(_float("TOPPREISE_TIMEOUT_MS", 45000))
    # Known plugin context hashes (stable across sessions); read from the page when possible.
    categorymenu_hash: str = os.environ.get("TOPPREISE_CATEGORYMENU_HASH", "849247345")
    pricechart_hash: str = os.environ.get("TOPPREISE_PRICECHART_HASH", "1329180698")


SETTINGS = Settings()
