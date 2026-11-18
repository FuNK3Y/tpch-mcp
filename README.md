# tpch-mcp

An MCP server for [toppreise.ch](https://www.toppreise.ch), the Swiss price comparison site, for personal use. It lets Claude find categories, apply the site's filters, list products, read offers and price history, and compare products.

## How it works
- **A real browser:** toppreise.ch rejects non-browser HTTP clients, so the server drives one long-lived Chromium (Playwright). It runs headed on a virtual display inside Docker, with no stealth patches.
- **Navigation vs. in-page calls:** HTML pages (product pages) are navigated to. Filters, result lists, the category menu, price history and autocomplete are called from inside the page with `fetch()`.
- **Polite pacing:** 5–10 s between page loads, 2–4 s between in-page calls, one request at a time.
- **Caching:** categories 24 h, filters 1 h, listings and products 10 min, price history 6 h.
- **Filters:** encoded into the site's `sfh` format (see `src/tpch_mcp/sfh.py`). Every response is checked against what the site echoes back, because the site silently drops filters it doesn't understand.

## Build & test
```bash
docker build -t tpch-mcp .
docker run --rm tpch-mcp pytest -q                                   # offline tests (fixtures)
docker run --rm --init --ipc=host tpch-mcp pytest -q -m live -s      # hits the real site (slow)
```

## CI & updates
- **`.github/workflows/docker.yml`:** runs on every push to `main`, on every PR, on `v*` tags, and weekly (Tuesday 04:00 UTC, refreshes Debian packages). It builds the image, runs the offline tests and an MCP smoke test inside it, then (except for PRs) pushes to **`ghcr.io/<owner>/tpch-mcp`** with tags `latest`, `main`, `sha-…` and `X.Y.Z`/`X.Y` for version tags. Live tests against toppreise.ch are not run in CI.
- **`.github/dependabot.yml`:** Dependabot defaults (one PR per update, weekly) for Python packages (`pyproject.toml`, exact pins), the Docker base image (tag and digest) and the GitHub Actions.
- **Dependabot PRs release automatically:** when a Dependabot PR passes `build`, the `release-dependabot` job merges it and starts the workflow on `main` (via `workflow_dispatch`), which publishes a new image. A merge made with `GITHUB_TOKEN` alone would not trigger the push build. Recommended: a branch rule on `main` that requires the `build` check.

Use the published image instead of a local build (run `docker login ghcr.io` first if the package is private):
```bash
claude mcp add tpch -- docker run -i --rm --init --ipc=host ghcr.io/<owner>/tpch-mcp:latest
```

## Use with Claude Code
```bash
claude mcp add tpch -- docker run -i --rm --init --ipc=host tpch-mcp
```

## Tools
| Tool | Purpose |
|---|---|
| `find_category(query)` | Free text → category ids (leaf categories have spec filters) |
| `list_categories(parent_id, depth)` | Browse the category tree |
| `get_filters(category_id, include_expert, filter_name, value_search)` | The category's filters, values, brands, price/year ranges |
| `browse(category_id, query, filters, brands, price_min/max, availability, min_year, sort, page)` | Filtered product list |
| `search(query)` | Picks a category for the words, then filters product names (the site's own full-text search is behind a bot check) |
| `get_top_list(list, category_id, hours, page, limit)` | Home-page lists: `top100`, `top_rated`, `new_top_prices` (last 1–48 h, default 48), `new_products`, site-wide or per category |
| `get_product(product_id)` | Details, data sheet, price range, cheapest offer |
| `get_offers(product_id, sort, in_stock_only, limit)` | All shop offers |
| `get_price_history(product_id, days)` | Price history summary |
| `compare_products(product_ids)` | Specs that differ, plus best prices |

**Resources:** `toppreise://categories`, `toppreise://categories/{id}/filters`, `toppreise://products/{id}`

**Prompts:** `best_deal`, `buy_now_or_wait`, `compare`

## Settings (environment variables)
| Variable | Default | Purpose |
|---|---|---|
| `TPCH_TRANSPORT` | `stdio` | `stdio` (Claude Code / Desktop) or `http` (remote, e.g. claude.ai) |
| `TPCH_HTTP_HOST` / `TPCH_HTTP_PORT` / `TPCH_HTTP_PATH` | `0.0.0.0` / `8000` / `/mcp` | HTTP listener |
| `TPCH_PUBLIC_HOST` | – | Public hostname (e.g. `tpch.example.ch`), accepted in the `Host` header |
| `TPCH_LANG` | `en` | Site language |
| `TPCH_NAV_DELAY_MIN/MAX` | `5` / `10` s | Pause between page loads |
| `TPCH_XHR_DELAY_MIN/MAX` | `2` / `4` s | Pause between in-page calls |
| `TPCH_SETTLE_MIN/MAX` | `0.3` / `0.7` s | Pause after a page load |
| `TPCH_TIMEOUT_MS` | `45000` | Per page / request |
| `TPCH_HEADLESS` | `0` | `1` = headless Chromium (not recommended) |

## Remote use (claude.ai) behind Traefik
The HTTP transport has **no built-in auth**. Expose it only through a reverse proxy that checks a
secret header. With Traefik, a router rule does it: requests without the exact header match no
router and get a 404.

```yaml
  tpch:
    image: ghcr.io/funk3y/tpch-mcp:latest
    restart: unless-stopped
    init: true
    shm_size: 1gb            # Chromium needs more than Docker's 64 MB default
    environment:
      - TPCH_TRANSPORT=http
      - TPCH_PUBLIC_HOST=tpch.${DOMAIN}
    labels:
      - traefik.enable=true
      - traefik.http.routers.tpch.rule=Host(`tpch.${DOMAIN}`) && Header(`X-Api-Key`, `${TPCH_API_KEY}`)
      - traefik.http.services.tpch.loadbalancer.server.port=8000
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=5)"]
      interval: 1m
      timeout: 10s
      retries: 3
```

Generate the key with `openssl rand -hex 32` and put it in `.env` as `TPCH_API_KEY=…`. In claude.ai, go to
**Settings → Connectors → Add custom connector**. Use URL `https://tpch.<domain>/mcp`, choose **No sign-in**,
and add a request header named `X-Api-Key` with the key as its value.

## Notes
- Test fixtures are real page captures (gzipped) from November 2026.
- For personal use only: keep the pacing slow.
