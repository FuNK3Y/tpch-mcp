"""Tiny in-memory TTL cache."""
import time
from typing import Any

# Default lifetimes in seconds.
TTL = {
    "categories": 24 * 3600,
    "filters": 3600,
    "listing": 600,
    "product": 600,
    "pricechart": 6 * 3600,
    "autocomplete": 3600,
}


class TTLCache:
    def __init__(self) -> None:
        self._data: dict[tuple, tuple[float, Any]] = {}

    def get(self, kind: str, *key: Any) -> Any | None:
        item = self._data.get((kind, *key))
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._data.pop((kind, *key), None)
            return None
        return value

    def set(self, kind: str, *key_and_value: Any) -> Any:
        *key, value = key_and_value
        self._data[(kind, *key)] = (time.monotonic() + TTL.get(kind, 600), value)
        return value
