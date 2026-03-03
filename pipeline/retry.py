"""Shared retry/backoff utility for transient API failures."""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Callable, TypeVar

import requests

logger = logging.getLogger(__name__)

T = TypeVar("T")

TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}


def retry_api_call(
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    transient_exceptions: tuple = (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
    ),
) -> Callable:
    """Decorator that retries a function on transient failures with exponential backoff.

    Retries on:
    - Network errors (ConnectionError, Timeout)
    - HTTP 429 (rate limit) and 5xx errors
    - OpenAI rate limit / server errors
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except transient_exceptions as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        raise
                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    logger.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        func.__qualname__, attempt + 1, max_retries + 1, exc, delay,
                    )
                    time.sleep(delay)
                except requests.exceptions.HTTPError as exc:
                    last_exc = exc
                    status = getattr(exc.response, "status_code", None)
                    if status in TRANSIENT_HTTP_CODES and attempt < max_retries:
                        retry_after = _parse_retry_after(exc.response)
                        delay = retry_after or min(base_delay * (backoff_factor ** attempt), max_delay)
                        logger.warning(
                            "%s HTTP %s (attempt %d/%d) — retrying in %.1fs",
                            func.__qualname__, status, attempt + 1, max_retries + 1, delay,
                        )
                        time.sleep(delay)
                    else:
                        raise
                except Exception as exc:
                    exc_type = type(exc).__name__
                    if _is_openai_transient(exc) and attempt < max_retries:
                        last_exc = exc
                        delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                        logger.warning(
                            "%s OpenAI transient error (attempt %d/%d): %s — retrying in %.1fs",
                            func.__qualname__, attempt + 1, max_retries + 1, exc, delay,
                        )
                        time.sleep(delay)
                    else:
                        raise
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


def _parse_retry_after(response) -> float | None:
    """Extract Retry-After header value in seconds."""
    if response is None:
        return None
    val = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if val:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return None


def _is_openai_transient(exc: Exception) -> bool:
    """Check if an OpenAI SDK exception is transient (rate limit or server error)."""
    exc_type = type(exc).__name__
    if exc_type in ("RateLimitError", "APIConnectionError", "InternalServerError", "APITimeoutError"):
        return True
    if "rate" in str(exc).lower() and "limit" in str(exc).lower():
        return True
    if "server" in str(exc).lower() and "error" in str(exc).lower():
        return True
    return False
