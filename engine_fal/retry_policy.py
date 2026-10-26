"""Request, retry and error policy — the retry half of the Engine, split out.

Mixin methods on Engine, mirroring the gold engine's `retry_policy.py`:
`_request_with_retry` keeps its exponential backoff with jitter, the
stretched high-demand backoff, and Retry-After honoring (`sleep` is at
least the requested value); the classifier is unchanged.

fal-specific: the provider's errors carry their response on the exception
(`exc.response.headers`), so the header lookup reads both shapes.
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
import random
import time
from typing import Any


def _headers_of(exc):
    """Return the response headers an exception carries, or None."""
    for holder in (getattr(exc, "response", None), exc):
        headers = getattr(holder, "headers", None)
        if headers is not None:
            return headers
    return None


def _retry_after_seconds(exc) -> float | None:
    """Seconds named by an exception's Retry-After header, or None.

    Guards every malformed shape: missing headers, None, a non-mapping (a
    string, a list), or an unparseable value. Numeric strings, ints/floats and
    the standard HTTP-date form are honored; backoff always sleeps at LEAST the
    requested value — the max semantics stay.
    """
    headers = _headers_of(exc)
    if headers is None or not hasattr(headers, "get"):
        return None
    value = None
    try:
        for key in headers:
            if str(key).lower() == "retry-after":
                value = headers.get(key)
                break
    except Exception:
        return None
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


class RetryPolicy:
    def _resolve_api_key(self) -> str:
        key = self._api_key or os.environ.get(self.API_KEY_ENV_VAR, "")
        if key and self.API_KEY_ENV_VAR not in os.environ:
            os.environ[self.API_KEY_ENV_VAR] = key
        return key

    def _request_with_retry(
        self, client, endpoint: str, payload: dict, prefix: str
    ) -> Any:
        max_retries = int(self._profile.get("retry_on_429", 4))
        base_delay = float(self._profile.get("retry_base_delay", 2.0))
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return client.subscribe(endpoint, arguments=payload)
            except Exception as exc:
                rate = self._classify_error(exc)
                if rate != "retryable" or attempt >= max_retries:
                    raise
                last_exc = exc
                # High-demand outages resolve slower than a plain 429 burst —
                # stretch the delay so we stop hammering the model.
                long_backoff = self._is_high_demand(exc)
                sleep = base_delay * (8 if long_backoff else 2) ** attempt + random.uniform(
                    0, 1
                )
                requested = _retry_after_seconds(exc)
                if requested is not None:
                    sleep = max(sleep, requested)
                label = "high-demand outage" if long_backoff else "429 rate limit"
                self._emit(
                    f"{prefix} ⏳ {label}, retry {attempt + 1}/{max_retries} in {sleep:.1f}s"
                )
                time.sleep(sleep)
        raise last_exc

    @classmethod
    def _classify_error(cls, exc: Exception) -> str:
        """Classify a provider exception.

        Returns one of ``"retryable"`` (transient load/rate/throttle), ``"fatal"``
        (billing, auth, quota — re-running will not help), or ``"other"``.
        """
        text = str(exc).lower()
        status = getattr(exc, "status", None)
        if status is None:
            status = getattr(exc, "status_code", None)
        if status in (402, 403, 401, 409):
            return "fatal"
        if any(
            token in text
            for token in (
                "insufficient",
                "payment",
                "billing",
                "balance",
                "quota",
                "no more",
                "unauthorized",
                "forbidden",
            )
        ):
            return "fatal"
        if status == 429:
            return "retryable"
        if cls._is_high_demand(exc):
            return "retryable"
        if any(
            token in text
            for token in ("ratelimit", "rate limit", "throttl", "too many")
        ):
            return "retryable"
        return "other"

    @staticmethod
    def _is_high_demand(exc: Exception) -> bool:
        """Detect provider 'service unavailable' / high-demand spikes."""
        text = str(exc).lower()
        return any(
            token in text
            for token in (
                "high demand",
                "currently unavailable",
                "e003",
                "503",
                "service unavailable",
            )
        )

    def _is_rate_limited(self, exc: Exception) -> bool:
        return self._classify_error(exc) == "retryable"

    def _is_fatal(self, exc: Exception) -> bool:
        return self._classify_error(exc) == "fatal"
