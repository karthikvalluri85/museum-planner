"""The only place that talks HTTP to the museums. Throttled, retried, and bounded by a request budget."""

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

USER_AGENT = "museum-planner-hackathon/0.1"
MAX_BACKOFF_SECONDS = 30.0


class UpstreamError(Exception):
    """The museum API failed in a way retrying did not fix."""


class HttpStatusError(UpstreamError):
    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"GET {url} returned HTTP {status}")
        self.status = status


class RequestBudgetExceeded(UpstreamError):
    """The per-museum cap on requests was reached."""


class HttpClient:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        requests_per_second: float = 10.0,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = 30.0,
    ) -> None:
        self._client = httpx.Client(
            transport=transport, timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        self._interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._budget: int | None = None
        self._used = 0

    def set_rate(self, requests_per_second: float) -> None:
        """Changes the throttle from now on, for an API that refuses faster clients."""
        with self._lock:
            self._interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0

    def reset_budget(self, max_requests: int) -> None:
        """Starts a new budget; every attempt, retries included, counts against it."""
        with self._lock:
            self._budget = max_requests
            self._used = 0

    @property
    def remaining(self) -> int | None:
        with self._lock:
            return None if self._budget is None else self._budget - self._used

    def get_json(
        self,
        url: str,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        last_error: Exception = UpstreamError(f"GET {url} was not attempted")
        retry_after: float | None = None
        for attempt in range(self._max_retries + 1):
            if attempt:
                backoff = 0.5 * 2 ** (attempt - 1) if retry_after is None else retry_after
                self._sleep(min(backoff, MAX_BACKOFF_SECONDS))
            self._acquire_slot()
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                last_error = UpstreamError(f"GET {url} failed: {exc}")
                retry_after = None
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = HttpStatusError(response.status_code, url)
                retry_after = _retry_after_seconds(response)
                continue
            if response.status_code >= 400:
                raise HttpStatusError(response.status_code, url)
            try:
                return response.json()
            except ValueError as exc:
                raise UpstreamError(f"GET {url} did not return JSON") from exc
        raise last_error

    def _acquire_slot(self) -> None:
        with self._lock:
            if self._budget is not None:
                if self._used >= self._budget:
                    raise RequestBudgetExceeded(f"request budget of {self._budget} exhausted")
                self._used += 1
            now = self._clock()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._interval
        if slot > now:
            self._sleep(slot - now)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    try:
        return max(0.0, float(response.headers.get("Retry-After", "")))
    except ValueError:
        return None
