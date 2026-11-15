"""Polite HTTP client: per-host rate limit, retries with backoff, optional gzip disk cache."""
from __future__ import annotations

import gzip
import hashlib
import threading
import time
from pathlib import Path

import httpx

from app.config import get_settings


class SourceError(RuntimeError):
    """A data source could not deliver usable data."""


class RateLimited(SourceError):
    pass


_lock = threading.Lock()
_last_call: dict[str, float] = {}


def _throttle(host: str, min_interval: float) -> None:
    with _lock:
        wait = _last_call.get(host, 0.0) + min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()


def fetch(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    min_interval: float = 0.0,
    timeout: float = 30.0,
    retries: int = 3,
    cache_path: Path | None = None,
    binary: bool = False,
) -> tuple[str | bytes, httpx.Headers | None]:
    """GET a URL. Returns (body, response headers); headers is None on a cache hit."""
    if cache_path is not None and cache_path.exists():
        raw = gzip.decompress(cache_path.read_bytes())
        return (raw if binary else raw.decode("utf-8")), None

    host = httpx.URL(url).host
    hdrs = {"User-Agent": get_settings().http_user_agent, **(headers or {})}
    last: Exception | None = None
    for attempt in range(retries):
        _throttle(host, min_interval)
        try:
            r = httpx.get(url, params=params, headers=hdrs, timeout=timeout, follow_redirects=True)
        except httpx.HTTPError as e:  # network-level failure: back off and retry
            last = e
            time.sleep(2 ** attempt * 2)
            continue
        if r.status_code == 429:
            # Never hammer a source that told us to stop.
            raise RateLimited(f"429 from {host} (Retry-After={r.headers.get('retry-after')})")
        if r.status_code >= 500:
            last = SourceError(f"{r.status_code} from {url}")
            time.sleep(2 ** attempt * 2)
            continue
        if r.status_code != 200:
            raise SourceError(f"{r.status_code} from {url}: {r.text[:200]}")
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(r.content, 6))
            tmp.replace(cache_path)
        return (r.content if binary else r.text), r.headers
    raise SourceError(f"giving up on {url}: {last}")


def url_key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]
