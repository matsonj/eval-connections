"""Retry utilities with exponential backoff, with special handling for HTTP 429.

- Uses exponential backoff by default.
- If an exception includes an HTTP response with status 429, honor the Retry-After header when present.
- Adds small jitter to reduce thundering herd.
"""

import time
import logging
import random
from typing import Callable, TypeVar
from functools import wraps

try:
    # Optional import; only used for isinstance checks
    import requests  # type: ignore
    from requests import HTTPError  # type: ignore
except Exception:  # pragma: no cover
    requests = None
    HTTPError = Exception  # type: ignore

T = TypeVar('T')

logger = logging.getLogger(__name__)


def _retry_after_seconds(exc: Exception) -> float | None:
    """Extract Retry-After seconds from an HTTPError if available."""
    try:
        if hasattr(exc, "response") and getattr(exc, "response") is not None:  # type: ignore[attr-defined]
            resp = getattr(exc, "response")  # type: ignore[attr-defined]
            # Retry-After may be seconds or HTTP date; we only handle seconds here
            retry_after = resp.headers.get("Retry-After")  # type: ignore[attr-defined]
            if retry_after is not None:
                try:
                    return float(retry_after)
                except ValueError:
                    # Not a simple seconds value; ignore
                    return None
            # Some providers use lowercase or alternative headers
            retry_after = resp.headers.get("retry-after")  # type: ignore[attr-defined]
            if retry_after is not None:
                try:
                    return float(retry_after)
                except ValueError:
                    return None
    except Exception:
        return None
    return None


def _status_code(exc: Exception) -> int | None:
    try:
        if hasattr(exc, "response") and getattr(exc, "response") is not None:  # type: ignore[attr-defined]
            return getattr(exc, "response").status_code  # type: ignore[attr-defined]
    except Exception:
        return None
    return None


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 2.0,
    exceptions: tuple = (Exception,)
) -> Callable:
    """
    Decorator for retrying functions with exponential backoff.

    - On HTTP 429, honors Retry-After (if present), otherwise falls back to exponential backoff.
    - Adds small jitter (0-0.5s) to spread concurrent retries.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:  # type: ignore[misc]
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(f"Max retries ({max_retries}) exceeded for {func.__name__}")
                        break

                    # Default exponential backoff
                    exp_delay = base_delay * (2 ** attempt)

                    # If 429, prefer Retry-After when available
                    sc = _status_code(e)
                    ra = _retry_after_seconds(e) if sc == 429 else None
                    delay = max(exp_delay, ra) if ra is not None else exp_delay

                    # Add jitter up to 0.5s
                    jitter = random.uniform(0.0, 0.5)
                    total_delay = delay + jitter

                    if sc == 429:
                        if ra is not None:
                            logger.warning(
                                f"Attempt {attempt + 1}/{max_retries + 1} failed for {func.__name__}: 429 Too Many Requests. "
                                f"Honoring Retry-After={ra}s; sleeping {total_delay:.2f}s..."
                            )
                        else:
                            logger.warning(
                                f"Attempt {attempt + 1}/{max_retries + 1} failed for {func.__name__}: 429 Too Many Requests. "
                                f"No Retry-After; using backoff {total_delay:.2f}s..."
                            )
                    else:
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_retries + 1} failed for {func.__name__}: {e}. "
                            f"Retrying in {total_delay:.2f}s..."
                        )

                    time.sleep(total_delay)

            # If we get here, all retries failed
            assert last_exception is not None
            raise last_exception

        return wrapper

    return decorator
