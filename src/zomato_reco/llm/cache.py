"""In-process LLM response cache.

Identical preference + candidate sets are the common case while iterating, and each
live Groq call is 2–4k tokens against an 8k/minute cap. A cache hit costs nothing.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any


class ResponseCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            hit = self._store.get(key)
            return None if hit is None else dict(hit)

    def put(self, key: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._store[key] = dict(payload)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


def cache_key(model: str, system: str, user: str, schema: dict[str, Any] | None) -> str:
    blob = json.dumps(
        {"model": model, "system": system, "user": user, "schema": schema or {}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


_cache = ResponseCache()


def get_cache() -> ResponseCache:
    return _cache
