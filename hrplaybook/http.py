"""HTTP client: httpx + tenacity retry + polite rate-limit + disk cache.

Network and parsing are deliberately separated. This client returns raw text
(or parsed JSON); the source modules own all parsing so they can be unit-tested
against recorded fixtures with zero network.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .cache import DiskCache


class Client:
    def __init__(
        self,
        cache: DiskCache,
        user_agent: str = "hrplaybook/1.0 (personal use)",
        rate_limit_per_sec: float = 1.0,
        timeout: float = 30.0,
        offline: bool = False,
    ):
        self.cache = cache
        self.user_agent = user_agent
        self.min_interval = 1.0 / rate_limit_per_sec if rate_limit_per_sec > 0 else 0.0
        self.offline = offline
        self._last_request = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "*/*"},
            timeout=timeout,
            follow_redirects=True,
        )
        self.network_errors = 0

    @staticmethod
    def _key(url: str, params: Optional[dict]) -> str:
        if not params:
            return url
        flat = []
        for k in sorted(params):
            v = params[k]
            if isinstance(v, (list, tuple)):
                for item in v:
                    flat.append((k, item))
            else:
                flat.append((k, v))
        return url + "?" + urllib.parse.urlencode(flat)

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        elapsed = time.time() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.time()

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.8, min=0.8, max=8),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    )
    def _raw_get(self, url: str, params: Optional[dict]) -> str:
        self._throttle()
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.text

    def get_text(
        self,
        namespace: str,
        url: str,
        params: Optional[dict] = None,
        allow_stale_on_error: bool = True,
    ) -> Optional[str]:
        key = self._key(url, params)
        cached = self.cache.get(namespace, key)
        if cached is not None:
            return cached
        if self.offline:
            return self.cache.get(namespace, key, allow_stale=True)
        try:
            text = self._raw_get(url, params)
        except Exception:
            self.network_errors += 1
            return self.cache.get(namespace, key, allow_stale=True)
        self.cache.set(namespace, key, text)
        return text

    def get_json(
        self,
        namespace: str,
        url: str,
        params: Optional[dict] = None,
        allow_stale_on_error: bool = True,
    ) -> Optional[dict]:
        text = self.get_text(namespace, url, params, allow_stale_on_error)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def close(self) -> None:
        self._client.close()
