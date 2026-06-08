"""Tiny disk cache keyed by (namespace, key) with per-namespace TTL.

Stores raw text/JSON payloads so re-runs (and fully offline runs) work. Each
entry is a sidecar JSON with a ``fetched_at`` epoch plus the payload.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Dict, Optional


class DiskCache:
    def __init__(self, root: str | Path, ttl_minutes: Optional[Dict[str, int]] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_minutes = ttl_minutes or {}
        self.hits = 0
        self.misses = 0
        self.stale_served = 0

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        ns_dir = self.root / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)
        return ns_dir / f"{digest}.json"

    def get(self, namespace: str, key: str, allow_stale: bool = False) -> Optional[str]:
        p = self._path(namespace, key)
        if not p.exists():
            self.misses += 1
            return None
        try:
            blob = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            self.misses += 1
            return None
        ttl = self.ttl_minutes.get(namespace)
        age_min = (time.time() - blob.get("fetched_at", 0)) / 60.0
        if ttl is not None and age_min > ttl:
            if allow_stale:
                self.stale_served += 1
                return blob.get("payload")
            self.misses += 1
            return None
        self.hits += 1
        return blob.get("payload")

    def set(self, namespace: str, key: str, payload: str) -> None:
        p = self._path(namespace, key)
        p.write_text(json.dumps({"fetched_at": time.time(), "payload": payload}))

    def summary(self) -> str:
        return (
            f"cache hits={self.hits} misses={self.misses} "
            f"stale_served={self.stale_served}"
        )
