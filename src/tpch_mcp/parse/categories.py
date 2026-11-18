"""Category tree from POST /plugins/category/categorymenu (plain JSON, flat list with parentID)."""
import html
import json
import re
import unicodedata
from dataclasses import dataclass, field

from .util import absolute


@dataclass
class Category:
    id: str
    name: str
    url: str
    parent: str | None
    children: list[str] = field(default_factory=list)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


class CategoryTree:
    def __init__(self, nodes: dict[str, Category]):
        self.nodes = nodes

    @classmethod
    def from_json(cls, raw: str | dict) -> "CategoryTree":
        data = json.loads(raw) if isinstance(raw, str) else raw
        nodes: dict[str, Category] = {}
        for n in data["categories"]:
            nodes[n["id"]] = Category(
                id=n["id"], name=html.unescape(n["title"]), url=absolute(n["url"]) or "",
                parent=None if n["id"] == "start" else (n.get("parentID") or "start"))
        for n in nodes.values():
            if n.parent and n.parent in nodes:
                nodes[n.parent].children.append(n.id)
        return cls(nodes)

    def path(self, cid: str) -> list[str]:
        out, cur = [], self.nodes.get(cid)
        while cur is not None and cur.id != "start":
            out.append(cur.name)
            cur = self.nodes.get(cur.parent) if cur.parent else None
        return out[::-1]

    def describe(self, cid: str) -> dict:
        n = self.nodes[cid]
        return {"id": n.id, "name": n.name, "path": " > ".join(self.path(cid)),
                "is_leaf": not n.children, "subcategories": len(n.children), "url": n.url}

    def subtree(self, cid: str, depth: int) -> dict:
        n = self.nodes[cid]
        d = {"id": n.id, "name": n.name, "is_leaf": not n.children}
        if n.children:
            if depth > 0:
                d["children"] = [self.subtree(c, depth - 1) for c in n.children]
            else:
                d["subcategories"] = len(n.children)
        return d

    def search(self, query: str, limit: int = 10, within: str | None = None) -> list[dict]:
        q = _norm(query)
        words = q.split()
        scored = []
        for n in self.nodes.values():
            if n.id == "start":
                continue
            if within and within not in self._ancestors(n.id):
                continue
            name = _norm(n.name)
            if not all(w in name for w in words):
                full = _norm(" ".join(self.path(n.id)))
                if not all(w in full for w in words):
                    continue
                score = 1
            else:
                score = 3 if name == q else 2
            score += 0.5 if not n.children else 0  # prefer leaves (they have spec filters)
            scored.append((score, -len(self.path(n.id)), n.id))
        scored.sort(reverse=True)
        return [self.describe(cid) for _, _, cid in scored[:limit]]

    def _ancestors(self, cid: str) -> set[str]:
        out, cur = {cid}, self.nodes.get(cid)
        while cur is not None and cur.parent:
            out.add(cur.parent)
            cur = self.nodes.get(cur.parent)
        return out
