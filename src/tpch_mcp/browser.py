"""One long-lived real Chromium session (Playwright).

Why a browser: toppreise.ch rejects non-browser HTTP clients (TLS fingerprinting) and runs JS
checks. A genuine Chromium passes without any stealth tricks.

Rules learned on the live site:
  * HTML pages must be *navigated to*; fetching page HTML via in-page fetch() returns 403.
  * Plugin endpoints (filters/results, category menu, price chart, autocomplete) work via
    in-page fetch() with ``X-Requested-With: XMLHttpRequest``.
All calls are serialized and paced (see config) to stay polite.
"""
import asyncio
import logging
import random
import time

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .config import SETTINGS, Settings

log = logging.getLogger(__name__)

_FETCH_JS = """async ([url, method, form]) => {
  const o = {method, credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}};
  if (form) {
    o.body = new URLSearchParams(form);
    o.headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8';
  }
  const r = await fetch(url, o);
  return {status: r.status, text: await r.text()};
}"""

# Cheap fingerprint of an element's content, to notice when the site re-renders it.
_SNAPSHOT_JS = "((s) => { const e = document.querySelector(s); return e ? e.innerHTML.length + ':' + e.innerHTML.slice(0, 2000) : ''; })"

_OFFERS_SETTLED_JS = """([s, expected, before]) => {
  const n = document.querySelectorAll(s).length, now = Date.now();
  const w = window.__tpchOffers || (window.__tpchOffers = {n: -1, t: now});
  if (n !== w.n) { w.n = n; w.t = now; }
  return n >= expected || (n > before && now - w.t > 1500);
}"""

_SET_OFFERS_PER_PAGE_JS = """(n) => {
  const i = document.querySelector('.Plugin_OffersPerPage .f_offersperpage_settings');
  if (!i) return false;
  if (i.value === String(n)) return true;
  i.value = String(n);
  i.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}"""


class BlockedError(RuntimeError):
    """The site refused us (403 / block page)."""


class ChallengeError(BlockedError):
    """The site shows a human-verification page (Turnstile/ALTCHA). We never try to solve it."""


def looks_blocked(html: str) -> bool:
    return "Error 403" in html[:3000] or "massive, ongoing attacks" in html or "massiver, anhaltender" in html


def looks_challenged(html: str) -> bool:
    head = html[:20000]
    return ("<title>Quick check</title>" in head or "data-turnstile-site-key" in html
            or "data-altcha-challenge" in html)


class BrowserSession:
    def __init__(self, settings: Settings = SETTINGS):
        self.s = settings
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None
        self._lock = asyncio.Lock()
        self._last = 0.0

    # ---------------- lifecycle ----------------
    async def _ensure(self) -> Page:
        if self._page is not None and not self._page.is_closed():
            return self._page
        log.info("starting Chromium (headless=%s)", self.s.headless)
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.s.headless)
        self._ctx = await self._browser.new_context(
            locale="en-GB" if self.s.lang == "en" else f"{self.s.lang}-CH",
            timezone_id="Europe/Zurich",
            viewport={"width": 1440, "height": 900},
        )
        self._page = await self._ctx.new_page()
        self._page.set_default_timeout(self.s.timeout_ms)
        await self._goto(f"/{self.s.lang}")
        return self._page

    async def close(self) -> None:
        for obj in (self._ctx, self._browser):
            try:
                if obj is not None:
                    await obj.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw is not None:
            await self._pw.stop()
        self._pw = self._browser = self._ctx = self._page = None

    # ---------------- pacing ----------------
    async def _pace(self, lo: float, hi: float) -> None:
        wait = self._last + random.uniform(lo, hi) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()

    # ---------------- primitives ----------------
    async def _goto(self, path: str) -> str:
        page = self._page
        assert page is not None
        await self._pace(self.s.nav_delay_min, self.s.nav_delay_max)
        url = path if path.startswith("http") else self.s.base_url + path
        resp = await page.goto(url, wait_until="domcontentloaded")
        await page.mouse.wheel(0, random.randint(300, 900))
        await asyncio.sleep(random.uniform(self.s.settle_min, self.s.settle_max))
        html = await page.content()
        if looks_challenged(html):
            raise ChallengeError(f"toppreise.ch shows a human-verification check for {path}; "
                                 "this server does not bypass it")
        if (resp is not None and resp.status == 403) or looks_blocked(html):
            raise BlockedError(f"toppreise.ch refused the page {path} (status {resp.status if resp else '?'})")
        if resp is not None and resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status} for {path}")
        return html

    async def navigate(self, path: str) -> str:
        """Load a full page like a user would and return its (JS-updated) HTML."""
        async with self._lock:
            await self._ensure()
            return await self._goto(path)

    async def xhr(self, path: str, form: dict | None = None, method: str | None = None) -> str:
        """Call a plugin endpoint from inside the page (same origin, same session)."""
        async with self._lock:
            page = await self._ensure()
            await self._pace(self.s.xhr_delay_min, self.s.xhr_delay_max)
            r = await page.evaluate(_FETCH_JS, [path, method or ("POST" if form else "GET"), form])
            if r["status"] == 403 or looks_blocked(r["text"][:5000]):
                raise BlockedError(f"toppreise.ch refused {path} (status {r['status']})")
            if r["status"] >= 400:
                raise RuntimeError(f"HTTP {r['status']} for {path}")
            return r["text"]

    async def _wait_for(self, js: str, arg, timeout_s: float) -> bool:
        """Wait (inside the page) until ``js(arg)`` is truthy; False on timeout."""
        try:
            await self._page.wait_for_function(js, arg=arg, timeout=timeout_s * 1000, polling=500)
        except PlaywrightTimeoutError:
            return False
        await asyncio.sleep(0.3)  # let the re-rendered list finish
        return True

    async def navigate_and_act(self, path: str, action_js: str, arg, watch_selector: str,
                               timeout_s: float = 15.0) -> tuple[str, bool]:
        """Load a page, run ``action_js(arg)`` in it (it returns true if it changed something),
        then wait until the element matching ``watch_selector`` has been re-rendered.
        Returns (html, changed)."""
        async with self._lock:
            page = await self._ensure()
            await self._goto(path)
            before = await page.evaluate(f"(s) => {_SNAPSHOT_JS}(s)", watch_selector)
            if not await page.evaluate(action_js, arg):
                return await page.content(), False
            changed = await self._wait_for(
                f"([s, b]) => {_SNAPSHOT_JS}(s) !== b", [watch_selector, before], timeout_s)
            return await page.content(), changed

    async def product_page(self, path: str, want_all_offers: bool = True) -> tuple[str, bool]:
        """Navigate to a product page; if the offer list is paginated, switch it to 96 per page.
        Returns (html, expanded)."""
        async with self._lock:
            page = await self._ensure()
            html = await self._goto(path)
            if not want_all_offers:
                return html, False
            sel = "div.Plugin_Offer.productDetailsOfferList"
            before = await page.locator(sel).count()
            expected = await page.evaluate("""() => {
              for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                try { const d = JSON.parse(s.textContent);
                      if (d['@type'] === 'Product' && d.offers) return Number(d.offers.offerCount) || 0; } catch (e) {}
              }
              return 0; }""")
            if before >= expected:
                return html, False
            ok = await page.evaluate(_SET_OFFERS_PER_PAGE_JS, 96)
            if not ok:
                return html, False
            # Done when every counted offer is rendered, or when the list has grown and then stayed
            # unchanged for 1.5 s (the site sometimes counts offers it does not list).
            await self._wait_for(_OFFERS_SETTLED_JS, [sel, expected, before], 15.0)
            return await page.content(), True
