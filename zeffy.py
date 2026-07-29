"""Minimal Zeffy API client.

Zeffy's public API is read-only and exposes payments, contacts and campaigns at
https://api.zeffy.com/api/v1/ with a bearer token. It is cursor paginated:
responses carry `has_more` and `next_cursor`, and the next page is requested
with `starting_after`. Documented rate limit is 100 requests/minute.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from typing import Any

import requests

log = logging.getLogger(__name__)

# The API is young and the exact envelope key is not contractually documented,
# so accept the plausible shapes rather than hard-failing on one.
_ITEM_KEYS = ("data", "items", "results", "campaigns", "records")
_CURSOR_KEYS = ("next_cursor", "nextCursor", "next_page_cursor")
_HAS_MORE_KEYS = ("has_more", "hasMore")

RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class ZeffyError(RuntimeError):
    """A Zeffy API call failed in a way we cannot recover from."""


class _RateLimiter:
    """Simple sliding-window limiter so we stay under the documented ceiling."""

    def __init__(self, max_per_minute: int) -> None:
        self.max_per_minute = max(1, max_per_minute)
        self._stamps: list[float] = []

    def acquire(self) -> None:
        now = time.monotonic()
        self._stamps = [t for t in self._stamps if now - t < 60.0]
        if len(self._stamps) >= self.max_per_minute:
            sleep_for = 60.0 - (now - self._stamps[0]) + 0.05
            if sleep_for > 0:
                log.info("Local rate limit reached, sleeping %.1fs", sleep_for)
                time.sleep(sleep_for)
            now = time.monotonic()
            self._stamps = [t for t in self._stamps if now - t < 60.0]
        self._stamps.append(time.monotonic())


def extract_items(payload: Any) -> list[dict]:
    """Pull the list of records out of a Zeffy list response."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in _ITEM_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def extract_cursor(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in _CURSOR_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def has_more(payload: Any, items: list[dict], page_size: int) -> bool:
    if isinstance(payload, dict):
        for key in _HAS_MORE_KEYS:
            if key in payload:
                return bool(payload[key])
    # No explicit flag: assume a full page means there may be another.
    return len(items) >= page_size


class ZeffyClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.zeffy.com/api/v1",
        max_requests_per_minute: int = 90,
        timeout: int = 30,
        max_retries: int = 5,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._limiter = _RateLimiter(max_requests_per_minute)
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "ammfs-zeffy-calendar-sync/1.0",
            }
        )

    def get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_error: str = ""
        for attempt in range(self.max_retries):
            self._limiter.acquire()
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                self._backoff(attempt, None)
                continue

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise ZeffyError(f"GET {url} returned non-JSON body: {exc}") from exc

            if resp.status_code in (401, 403):
                # Never echo the key or the body, which may quote it back.
                raise ZeffyError(
                    f"GET {path} rejected with {resp.status_code}. The Zeffy API key "
                    "is missing, revoked, or lacks access to this resource."
                )

            if resp.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}"
                self._backoff(attempt, resp.headers.get("Retry-After"))
                continue

            raise ZeffyError(
                f"GET {path} failed with HTTP {resp.status_code}: {resp.text[:400]}"
            )

        raise ZeffyError(
            f"GET {path} failed after {self.max_retries} attempts ({last_error})"
        )

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = 2.0 ** attempt
        else:
            delay = 2.0 ** attempt
        delay = min(delay, 60.0) + random.uniform(0, 0.5)
        log.warning("Retrying Zeffy request in %.1fs (attempt %d)", delay, attempt + 1)
        time.sleep(delay)

    def iter_campaigns(self, page_size: int = 100, max_pages: int = 200) -> Iterator[dict]:
        """Yield every campaign, following cursor pagination."""
        cursor: str | None = None
        seen_cursors: set[str] = set()

        for page in range(max_pages):
            params: dict[str, Any] = {"limit": min(page_size, 100)}
            if cursor:
                params["starting_after"] = cursor

            payload = self.get("campaigns", params=params)
            items = extract_items(payload)
            if page == 0 and not items:
                log.warning(
                    "Zeffy returned no campaigns on the first page. Envelope keys: %s",
                    list(payload) if isinstance(payload, dict) else type(payload).__name__,
                )
            yield from items

            if not has_more(payload, items, params["limit"]):
                return

            next_cursor = extract_cursor(payload)
            if not next_cursor:
                # has_more was true but no cursor came back; fall back to the
                # last record's id, and stop if we cannot find one.
                next_cursor = str(items[-1].get("id", "")) if items else ""
            if not next_cursor or next_cursor in seen_cursors:
                log.warning("Pagination cursor did not advance; stopping at page %d", page)
                return
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        log.warning("Hit max_pages=%d while listing campaigns", max_pages)
