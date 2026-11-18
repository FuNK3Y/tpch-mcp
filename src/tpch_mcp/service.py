"""Business logic behind the MCP tools: fetch (via BrowserSession) + parse + cache."""
import json
import logging
import random
import re
import string
from datetime import date, timedelta
from urllib.parse import quote, urlsplit

from . import sfh as sfhmod
from .browser import BrowserSession
from .cache import TTLCache
from .config import SETTINGS
from .parse.autocomplete import parse_autocomplete
from .parse.categories import CategoryTree
from .parse.filters import FilterError, FilterSet
from .parse.listing import parse_listing
from .parse.pricechart import parse_pricechart
from .parse.product import parse_product
from .parse.toplist import LISTS as TOPLISTS
from .parse.toplist import PERIODS_H, parse_toplist

_SET_PERIOD_JS = """(h) => {
  const box = document.querySelector('.Plugin_TimePeriod');
  const r = box && box.querySelector('input.f_timePeriod[value="' + h + '"]');
  if (!r) return false;
  const already = r.checked;
  // keep the HTML attribute in sync with the live state so the parsed HTML reports the real period
  box.querySelectorAll('input.f_timePeriod').forEach(i => i.removeAttribute('checked'));
  r.setAttribute('checked', 'checked');
  if (already) return false;
  r.checked = true;
  r.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
}"""

log = logging.getLogger(__name__)

AVAILABILITY_INPUT = {
    "in_stock": 1, "external_warehouse": 2, "within_week": 3, "within_two_weeks": 4, "any": None,
}
SORT_INPUT = {
    "price_asc": "pa", "price_desc": "pd", "popular": "rd", "newest": "td",
    "most_offers": "od", "availability": "va", "name_asc": "ba", "name_desc": "bd",
}


def _token() -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(18))


RATING_SCALE = 6  # toppreise shop ratings go from 1 to 6 (Swiss school grades), not 1-5


def product_id_of(value: int | str) -> int:
    """Accept 779730, "779730", "p779730" or a full toppreise.ch product URL."""
    if isinstance(value, int):
        return value
    s = str(value).strip()
    m = re.search(r"-p(\d+)(?:[/?#]|$)", s) or re.fullmatch(r"p?(\d+)", s, flags=re.I)
    if not m:
        raise ValueError(f"Not a toppreise product id or URL: {value!r} (expected e.g. 779730, p779730 "
                         "or https://www.toppreise.ch/price-comparison/...-p779730)")
    return int(m.group(1))


def rating_text(r: float | None) -> str | None:
    return f"{r:g}/{RATING_SCALE}" if r else None


def _path(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


class Toppreise:
    def __init__(self, session: BrowserSession | None = None):
        self.b = session or BrowserSession()
        self.cache = TTLCache()
        self.page_size = 24  # site default for a fresh session; updated from responses
        self.product_paths: dict[int, str] = {}

    async def close(self) -> None:
        await self.b.close()

    # ------------------------------------------------------------ categories
    async def categories(self) -> CategoryTree:
        tree = self.cache.get("categories")
        if tree is None:
            raw = await self.b.xhr("/plugins/category/categorymenu",
                                   {"c_cm_ch": SETTINGS.categorymenu_hash, "c_cm_la": SETTINGS.lang,
                                    "lang": SETTINGS.lang})
            tree = self.cache.set("categories", CategoryTree.from_json(raw))
        return tree

    async def _category(self, category_id: str):
        tree = await self.categories()
        cid = category_id if category_id.startswith("c") else f"c{category_id}"
        if cid not in tree.nodes:
            raise ValueError(f"Unknown category {category_id!r}; use find_category or list_categories")
        return tree, tree.nodes[cid]

    async def list_categories(self, parent_id: str = "c200", depth: int = 1) -> dict:
        tree, node = await self._category(parent_id)
        return {"category": tree.describe(node.id), "tree": tree.subtree(node.id, max(0, min(depth, 3)))}

    async def autocomplete(self, query: str) -> dict:
        hit = self.cache.get("autocomplete", query.lower())
        if hit is None:
            raw = await self.b.xhr(f"/plugins/generalelements/JsAutocomplete?type=search&q={quote(query)}&accid=")
            hit = self.cache.set("autocomplete", query.lower(), parse_autocomplete(raw))
        return hit

    async def find_category(self, query: str, limit: int = 8) -> dict:
        tree = await self.categories()
        local = tree.search(query, limit=limit)
        suggested = []
        try:
            ac = await self.autocomplete(query)
            for c in ac["categories"]:
                if c["category_id"] in tree.nodes:
                    d = tree.describe(c["category_id"])
                    d["source"] = "site search suggestion"
                    suggested.append(d)
        except Exception as e:  # noqa: BLE001 - autocomplete is a bonus
            log.warning("autocomplete failed: %s", e)
        seen, merged = set(), []
        for d in suggested + local:
            if d["id"] not in seen:
                seen.add(d["id"])
                merged.append(d)
        return {"query": query, "categories": merged[:limit]}

    # ------------------------------------------------------------ filters + results
    async def _reload(self, *, category_id: str | None, sfh: str, query: str = "",
                      content: bool, expert: bool = False) -> tuple[FilterSet, dict | None]:
        if category_id:
            _, node = await self._category(category_id)
            current = _path(node.url)
        else:
            current = f"/browse?q={quote(query)}"
        form = {
            "q": query or "", "fi_ses_ses": "Expert" if expert else "Standard",
            "dataCurrentUrl": current, "lang": SETTINGS.lang, "trigger": "initFilter",
            "fi_reload_page": _token(), "fi_reload_seq": "1", "JSONreloadFilter": "true",
            "JSONreloadContent": "true" if content else "false", "initFilter": "true",
        }
        if category_id:
            form["fi_cidh_cid"] = node.id
        if sfh:
            form["sfh"] = sfh
        raw = await self.b.xhr("/browse/reload_page_content", form)
        data = json.loads(raw)
        fs = FilterSet(data.get("sFilter") or {})
        listing = None
        c = data.get("sContent")
        if content and isinstance(c, dict) and c.get("sHTML"):
            listing = parse_listing(c["sHTML"])
            if listing.get("page_size"):
                self.page_size = listing["page_size"]
        return fs, listing

    async def filter_defs(self, category_id: str, expert: bool = True) -> FilterSet:
        key = (category_id, expert)
        fs = self.cache.get("filters", *key)
        if fs is None:
            fs, _ = await self._reload(category_id=category_id, sfh="e~1" if expert else "",
                                       content=False, expert=expert)
            self.cache.set("filters", *key, fs)
        return fs

    async def get_filters(self, category_id: str, include_expert: bool = False,
                          filter_name: str | None = None, value_search: str | None = None) -> dict:
        tree, node = await self._category(category_id)
        fs = await self.filter_defs(node.id, expert=True)
        if filter_name:
            return {"category": tree.describe(node.id), **fs.values_of(filter_name, value_search)}
        return {"category": tree.describe(node.id), **fs.compact(include_expert=include_expert)}

    async def browse(self, category_id: str | None = None, query: str | None = None,
                     filters: dict | None = None, brands: list[str] | None = None,
                     price_min: float | None = None, price_max: float | None = None,
                     availability: str | None = None, min_year: int | None = None,
                     sort: str | None = None, page: int = 1, include_uncategorised: bool = False,
                     max_variants: int = 3) -> dict:
        if not category_id:
            raise ValueError(
                "browse needs a category_id. Free-text search across the whole site is behind a human-"
                "verification check on toppreise.ch; use search(query) or find_category(query) to pick a "
                "category, then browse(category_id, query=...) to filter by text inside it.")
        page = max(1, int(page or 1))
        applied: list[str] = []
        sel = sfhmod.Selection()
        order: list[str] = []
        if category_id:
            tree, node = await self._category(category_id)
            category_id = node.id
            if filters or brands:
                fs = await self.filter_defs(category_id, expert=True)
                order = fs.order
                try:
                    sel.props, applied = fs.resolve(filters or {})
                    sel.manufacturers, blabels = fs.brand_ids(brands)
                except FilterError as e:
                    raise ValueError(str(e)) from None
                if blabels:
                    applied.append(f"brand any of {blabels}")
                expert_ids = {f.id for f in fs.filters if f.expert}
                sel.expert = any(p[1] in expert_ids for p in sel.props)
        if query:
            # Sent as the in-category text filter (sfh "pt~"). A plain search query ("q") triggers the
            # site's bot check, the text filter does not.
            sel.text = query.strip()
            applied.append(f'name contains "{sel.text}"')
        if price_min is not None:
            sel.price_min = float(price_min); applied.append(f"price >= CHF {price_min}")
        if price_max is not None:
            sel.price_max = float(price_max); applied.append(f"price <= CHF {price_max}")
        if availability:
            if availability not in AVAILABILITY_INPUT:
                raise ValueError(f"availability must be one of {list(AVAILABILITY_INPUT)}")
            sel.max_availability = AVAILABILITY_INPUT[availability]
            if sel.max_availability:
                applied.append(f"availability up to '{availability}'")
        if min_year:
            sel.min_year = int(min_year); applied.append(f"released {min_year} or later")
        if sort:
            if sort not in SORT_INPUT:
                raise ValueError(f"sort must be one of {list(SORT_INPUT)}")
            sel.sort = SORT_INPUT[sort]
        sel.offset = (page - 1) * self.page_size
        sent = sfhmod.encode(sel, order)

        cache_key = (category_id, sent)
        hit = self.cache.get("listing", *cache_key)
        if hit is None:
            fs_res, listing = await self._reload(category_id=category_id, sfh=sent,
                                                 content=True, expert=sel.expert)
            if listing and page > 1 and listing["page"] != page and listing.get("page_size") \
                    and sel.offset != (page - 1) * listing["page_size"]:
                sel.offset = (page - 1) * listing["page_size"]  # page size learned now, retry once
                sent = sfhmod.encode(sel, order)
                fs_res, listing = await self._reload(category_id=category_id, sfh=sent,
                                                     content=True, expert=sel.expert)
            hit = self.cache.set("listing", *cache_key, (fs_res, listing))
        fs_res, listing = hit

        warnings = []
        ok, diffs = sfhmod.same_selection(sent, fs_res.echo)
        if not ok:
            warnings += diffs
        listing = listing or {"page": 1, "pages": 1, "items": []}
        items = []
        hidden_offers = 0
        for it in listing["items"]:
            if it["type"] == "offer" and not include_uncategorised:
                hidden_offers += 1
                continue
            if it["type"] == "offer":
                it = it | {"shop_rating": rating_text(it.get("shop_rating"))}
            if it["type"] == "series":
                for v in it["variants"]:
                    self._remember(v)
                extra = len(it["variants"]) - max_variants
                it = {**it, "variants": it["variants"][:max_variants]}
                if extra > 0:
                    it["more_variants_on_page"] = extra
            elif it["type"] == "product":
                self._remember(it)
            items.append(it)
        if hidden_offers:
            warnings.append(f"{hidden_offers} uncategorised shop offers hidden (include_uncategorised=true to show)")
        if fs_res.matches == 0 or (not items and not hidden_offers):
            warnings.append("No results - loosen the filters.")
        res = {
            "matches": fs_res.matches, "page": listing["page"], "pages": listing["pages"],
            "applied": applied, "items": items,
        }
        if category_id:
            res["category"] = (await self.categories()).describe(category_id)
        if query:
            res["query"] = query
        if warnings:
            res["warnings"] = warnings
        return res

    async def search(self, query: str, page: int = 1, sort: str | None = None) -> dict:
        """Free text -> best category (site autocomplete, then local tree) -> browse with a text filter.
        (The site's own full-text search is behind a human-verification check, so it is not used.)"""
        tree = await self.categories()
        candidates: list[dict] = []
        try:
            for c in (await self.autocomplete(query))["categories"]:
                if c["category_id"] in tree.nodes:
                    candidates.append(tree.describe(c["category_id"]) | {"source": "site suggestion"})
        except Exception as e:  # noqa: BLE001
            log.warning("autocomplete failed: %s", e)
        for c in tree.search(query, limit=5):
            if c["id"] not in {x["id"] for x in candidates}:
                candidates.append(c | {"source": "category name match"})
        if not candidates:
            return {"query": query, "matches": 0, "items": [],
                    "warnings": ["No matching category found; try find_category with other words."]}
        chosen = next((c for c in candidates if c["is_leaf"]), candidates[0])
        res = await self.browse(chosen["id"], query=query, page=page, sort=sort)
        if not res["matches"]:
            res = await self.browse(chosen["id"], page=page, sort=sort)
            res.setdefault("warnings", []).append(
                f'No product names contain "{query}" in this category; showing the whole category instead.')
        res["other_categories"] = [c for c in candidates if c["id"] != chosen["id"]][:5]
        res["tip"] = ("Results come from the category above with a text filter. Use get_filters + "
                      "browse(category_id, ...) to narrow down, or pick one of other_categories.")
        return res

    async def get_top_list(self, kind: str = "top100", category_id: str | None = None, hours: int = 48,
                           page: int = 1, limit: int = 24) -> dict:
        """Home-page lists: top100 | top_rated | new_top_prices | new_products (optionally per category)."""
        if kind not in TOPLISTS:
            raise ValueError(f"list must be one of {list(TOPLISTS)}")
        if hours not in PERIODS_H:
            raise ValueError(f"hours must be one of {list(PERIODS_H)}")
        prefix = TOPLISTS[kind][0]
        cat = None
        path = prefix
        if category_id:
            tree, node = await self._category(category_id)
            cat = tree.describe(node.id)
            path = prefix + _path(node.url).removeprefix("/browse")
        page = max(1, int(page or 1))
        if page > 1:
            path += f"?p={page - 1}"
        key = (kind, path, hours if kind == "new_top_prices" else None)
        data = self.cache.get("listing", *key)
        if data is None:
            if kind == "new_top_prices":
                # A fresh session defaults to 8 h, so always select the requested period (default 48 h).
                html, _changed = await self.b.navigate_and_act(path, _SET_PERIOD_JS, hours,
                                                               f".{TOPLISTS[kind][1]}")
                data = parse_toplist(html, kind)
                if data.get("period_hours") != hours:
                    data["warning"] = f"Could not switch the period to {hours} h; showing {data.get('period_hours')} h."
            else:
                data = parse_toplist(await self.b.navigate(path), kind)
            self.cache.set("listing", *key, data)
        for it in data["items"]:
            self._remember(it)
        res = {k: v for k, v in data.items() if k != "items"}
        res["category"] = cat
        res["count_on_page"] = len(data["items"])
        res["items"] = data["items"][:max(1, min(limit, 96))]
        if kind == "new_top_prices" and not category_id:
            res["note"] = ("Without a category the site only lists price drops of popular products; "
                           "pass category_id to see all price drops in a category.")
        if kind == "top_rated":
            res["rating_scale"] = "user ratings out of 6 (6 = best)"
        return res

    def _remember(self, row: dict) -> None:
        if row.get("id") and row.get("url"):
            self.product_paths[int(row["id"])] = _path(row["url"])

    # ------------------------------------------------------------ products
    async def _product(self, product_id: int | str) -> dict:
        pid = product_id_of(product_id)
        hit = self.cache.get("product", pid)
        if hit is not None:
            return hit
        path = self.product_paths.get(pid) or f"/price-comparison/-p{pid}"
        html, expanded = await self.b.product_page(path)
        data = parse_product(html)
        data["offers_complete"] = data["price"]["offer_count"] is None or \
            len(data["offers"]) >= (data["price"]["offer_count"] or 0)
        if data.get("url"):
            self.product_paths[pid] = _path(data["url"])
        return self.cache.set("product", pid, data)

    async def get_product(self, product_id: int) -> dict:
        d = await self._product(product_id)
        offers = sorted([o for o in d["offers"] if o["total"] is not None], key=lambda o: o["total"])
        best = offers[0] if offers else None
        return {k: v for k, v in d.items() if k not in ("offers", "offers_complete")} | {
            "best_offer": best and {k: best[k] for k in ("shop", "total", "price", "shipping", "availability", "url")},
        }

    async def get_offers(self, product_id: int, sort: str = "total", in_stock_only: bool = False,
                         limit: int = 20) -> dict:
        d = await self._product(product_id)
        offers = list(d["offers"])
        if in_stock_only:
            offers = [o for o in offers if o["availability"]["level"] == 1]
        keyf = {
            "total": lambda o: (o["total"] is None, o["total"] or 0),
            "price": lambda o: (o["price"] is None, o["price"] or 0),
            "availability": lambda o: (o["availability"]["level"] or 99, o["total"] or 0),
            "rating": lambda o: (-(o["shop_rating"] or 0), o["total"] or 0),
        }.get(sort)
        if keyf is None:
            raise ValueError("sort must be one of total, price, availability, rating")
        offers.sort(key=keyf)
        res = {
            "product": {"id": d["id"], "name": d["name"], "url": d["url"]},
            "offer_count": d["price"]["offer_count"], "shown": min(limit, len(offers)),
            "offers": [o | {"shop_rating": rating_text(o["shop_rating"])} for o in offers[:limit]],
            "note": "shop_rating is out of 6 (Swiss grades: 6 = best). Compare offers by 'total'.",
        }
        if not d["offers_complete"]:
            res["warning"] = (f"The product page lists {len(d['offers'])} of the {d['price']['offer_count']} offers "
                              f"the site counts (some are not shown in its offer list); the cheapest listed are included.")
        return res

    async def get_price_history(self, product_id: int | str, days: int = 90) -> dict:
        pid = product_id_of(product_id)
        days = max(7, min(int(days), 365))
        raw = self.cache.get("pricechart", pid)
        if raw is None:
            raw = await self.b.xhr("/plugins/product/pricechart", {
                "pcspagdpi": str(pid), "pcspagdfdt": (date.today() - timedelta(days=365)).isoformat(),
                "pcspagdtd": "", "p_pc_ch": SETTINGS.pricechart_hash, "lang": SETTINGS.lang})
            self.cache.set("pricechart", pid, raw)
        return {"product_id": pid, **parse_pricechart(raw, days=days)}

    async def compare_products(self, product_ids: list[int | str]) -> dict:
        if not 2 <= len(product_ids) <= 5:
            raise ValueError("Compare between 2 and 5 products")
        prods = [await self._product(p) for p in product_ids]
        keys: list[str] = []
        for p in prods:
            for group, rows in p["specs"].items():
                for k in rows:
                    key = f"{group} / {k}"
                    if key not in keys:
                        keys.append(key)
        table = {}
        for key in keys:
            g, k = key.split(" / ", 1)
            vals = [p["specs"].get(g, {}).get(k) for p in prods]
            if len({v for v in vals}) > 1 or len(prods) == 1:
                table[key] = vals
        same = len(keys) - len(table)
        return {
            "products": [{"id": p["id"], "name": p["name"], "best_total": min(
                [o["total"] for o in p["offers"] if o["total"] is not None], default=p["price"]["min"]),
                "offers": p["price"]["offer_count"], "url": p["url"]} for p in prods],
            "differences": table,
            "identical_specs_omitted": same,
        }
