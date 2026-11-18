# Plain Python base, pinned by digest. Dependabot bumps the Python version in the tag and proposes
# a PR whenever the image behind the tag is rebuilt (Python patch releases, Debian updates, a future
# Debian release), and CI tests it before it is merged. Playwright downloads the Chromium build that
# matches its pinned pip version, so the base image and the playwright package move independently.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

# Xvfb: Chromium runs headed on a virtual display (looks like a normal browser).
# libatomic1: needed by Playwright's bundled Node.js driver (missing from the slim image).
RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb libatomic1 \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[dev]" \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY tests ./tests
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

LABEL org.opencontainers.image.title="tpch-mcp" \
      org.opencontainers.image.description="MCP server for toppreise.ch (Swiss price comparison), personal use"

# HTTP mode: probes /healthz. stdio mode (no listener): always healthy.
HEALTHCHECK --interval=1m --timeout=10s --start-period=30s --retries=3 \
  CMD ["python", "-m", "tpch_mcp.healthcheck"]

# stdio MCP server by default; any other command (e.g. `pytest -q`) runs with the display available.
ENTRYPOINT ["docker-entrypoint.sh"]
