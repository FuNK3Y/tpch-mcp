"""Docker HEALTHCHECK: probe /healthz in HTTP mode; stdio mode has no listener, so it is healthy
as long as the process runs. Exit code 0 = healthy, 1 = unhealthy."""
import sys
import urllib.request

from .config import SETTINGS


def main() -> int:
    if SETTINGS.transport != "http":
        return 0
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{SETTINGS.http_port}/healthz", timeout=5) as r:
            return 0 if r.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
