"""Small, process-local cache for authorized semantic-search responses.

The database authorization fingerprint is part of every key. This prevents a
stale response from being reused even if an invalidation call is missed.
"""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from time import monotonic


@dataclass(frozen=True)
class CacheKey:
    user_id: int
    permission_version: int
    role_name: str
    authorized_documents: tuple[tuple[int, int], ...]
    query: str
    top_k: int


class RetrievalCache:
    def __init__(self, max_entries: int = 100, ttl_seconds: int = 300) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[
            CacheKey, tuple[float, list[dict[str, object]]]
        ] = OrderedDict()
        self._lock = RLock()

    def get(self, key: CacheKey) -> list[dict[str, object]] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, results = entry
            if expires_at <= monotonic():
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return deepcopy(results)

    def put(self, key: CacheKey, results: list[dict[str, object]]) -> None:
        with self._lock:
            self._entries[key] = (
                monotonic() + self.ttl_seconds,
                deepcopy(results),
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


semantic_retrieval_cache = RetrievalCache()
