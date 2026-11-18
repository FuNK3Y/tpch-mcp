"""Price history from POST /plugins/product/pricechart: [[ [ts_ms, price], ... ], ...]."""
import json
from datetime import datetime, timezone


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def parse_pricechart(raw: str | list, days: int = 365, samples: int = 24) -> dict:
    data = json.loads(raw) if isinstance(raw, str) else raw
    # Two series observed (one daily over a year, one finer over ~6 months): merge by day.
    by_day: dict[str, float] = {}
    for series in data or []:
        for ts, p in series:
            if p is not None:
                by_day[_day(int(ts))] = float(p)
    pts = sorted(by_day.items())
    if not pts:
        return {"points": 0, "note": "no price history available"}
    if days and len(pts) > days:
        pts = pts[-days:]
    prices = [p for _, p in pts]
    cur_day, cur = pts[-1]
    lo_day, lo = min(pts, key=lambda x: (x[1], x[0]))
    hi_day, hi = max(pts, key=lambda x: (x[1], x[0]))

    def ago(n: int) -> float | None:
        if len(pts) > n:
            return pts[-1 - n][1]
        return pts[0][1] if len(pts) >= n else None  # window exactly covers the requested period

    def change(old: float | None) -> float | None:
        return round((cur - old) / old * 100, 1) if old else None

    step = max(1, len(pts) // samples)
    # sample evenly, but always keep the first, last, lowest and highest day
    keep = set(pts[::step]) | {pts[0], pts[-1], (lo_day, lo), (hi_day, hi)}
    sampled = sorted(keep)
    return {
        "currency": "CHF",
        "from": pts[0][0], "to": cur_day, "points": len(pts),
        "current": cur,
        "lowest": {"price": lo, "date": lo_day},
        "highest": {"price": hi, "date": hi_day},
        "average": round(sum(prices) / len(prices), 2),
        "change_pct": {"7d": change(ago(7)), "30d": change(ago(30)), "90d": change(ago(90))},
        "is_at_lowest": cur <= lo + 0.01,
        "series": [{"date": d, "price": p} for d, p in sampled],
    }
