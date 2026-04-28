"""Thread-safe AIMD token-bucket rate limiter with concurrency cap.

All worker threads share one limiter per model. Each call to chat():
  1. Acquires a concurrency permit (BoundedSemaphore).
  2. Waits for a token to refill in the bucket (rps-paced).
  3. On 200 → on_success() additively grows rps toward rps_max.
  4. On 429 → on_429() halves rps (down to rps_min), drains remaining
     tokens, and honors Retry-After if the provider sent one.

This is the same control loop TCP uses for congestion (AIMD): grow slowly
while things are fine, back off hard when the network pushes back. It
self-tunes so a single magic rps value isn't required per provider.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class _Bucket:
    rps: float
    rps_max: float
    rps_min: float
    burst: float
    aimd_step: float
    tokens: float = field(init=False)
    last_refill: float = field(init=False)
    lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self.tokens = self.burst
        self.last_refill = time.monotonic()


@dataclass
class _Policy:
    rps: float = 8.0
    rps_max: float = 16.0
    rps_min: float = 0.5
    burst: float = 8.0
    aimd_step: float = 0.5
    concurrency: int = 8


class RateLimiter:
    """Per-model AIMD token bucket + concurrency cap, shared across threads."""

    def __init__(self, default_policy: Optional[_Policy] = None) -> None:
        self._default = default_policy or _Policy()
        self._policies: Dict[str, _Policy] = {}
        self._buckets: Dict[str, _Bucket] = {}
        self._semaphores: Dict[str, threading.BoundedSemaphore] = {}
        self._lock = threading.Lock()

    def configure(self, model_or_pattern: str, policy: _Policy) -> None:
        with self._lock:
            self._policies[model_or_pattern] = policy

    def _resolve_policy(self, model: str) -> _Policy:
        if model in self._policies:
            return self._policies[model]
        # Pattern-match by suffix (":free") or prefix (e.g. "poolside/").
        for pattern, policy in self._policies.items():
            if pattern.startswith(":") and model.endswith(pattern):
                return policy
            if pattern.endswith("/") and model.startswith(pattern):
                return policy
        return self._default

    def _get(self, model: str) -> tuple[_Bucket, threading.BoundedSemaphore]:
        with self._lock:
            if model not in self._buckets:
                policy = self._resolve_policy(model)
                self._buckets[model] = _Bucket(
                    rps=policy.rps,
                    rps_max=policy.rps_max,
                    rps_min=policy.rps_min,
                    burst=policy.burst,
                    aimd_step=policy.aimd_step,
                )
                self._semaphores[model] = threading.BoundedSemaphore(policy.concurrency)
            return self._buckets[model], self._semaphores[model]

    def acquire(self, model: str) -> None:
        bucket, sem = self._get(model)
        sem.acquire()
        try:
            while True:
                with bucket.lock:
                    now = time.monotonic()
                    elapsed = now - bucket.last_refill
                    bucket.last_refill = now
                    bucket.tokens = min(bucket.burst, bucket.tokens + elapsed * bucket.rps)
                    if bucket.tokens >= 1.0:
                        bucket.tokens -= 1.0
                        return
                    deficit = 1.0 - bucket.tokens
                    wait = deficit / max(bucket.rps, 1e-3)
                # Sleep outside the lock so other threads can refill / acquire.
                time.sleep(min(wait, 1.0))
        except BaseException:
            sem.release()
            raise

    def release(self, model: str) -> None:
        sem = self._semaphores.get(model)
        if sem is None:
            return
        try:
            sem.release()
        except ValueError:
            # Already at max — happens if a release races with acquire failure cleanup.
            pass

    def on_success(self, model: str) -> None:
        bucket, _ = self._get(model)
        with bucket.lock:
            new = min(bucket.rps + bucket.aimd_step, bucket.rps_max)
            if new != bucket.rps:
                bucket.rps = new

    def on_429(self, model: str, retry_after: Optional[float] = None) -> None:
        bucket, _ = self._get(model)
        with bucket.lock:
            old = bucket.rps
            bucket.rps = max(bucket.rps / 2.0, bucket.rps_min)
            bucket.tokens = 0.0
        logger.warning(
            f"[ratelimit] 429 on {model}: rps {old:.2f} → {bucket.rps:.2f}"
            + (f" (Retry-After={retry_after:.1f}s)" if retry_after else "")
        )
        if retry_after and retry_after > 0:
            time.sleep(retry_after)

    def snapshot(self, model: str) -> Dict[str, float]:
        bucket, _ = self._get(model)
        with bucket.lock:
            return {"rps": bucket.rps, "tokens": bucket.tokens, "burst": bucket.burst}


# -----------------------------------------------------------------------------
# Default instance + YAML config loader
# -----------------------------------------------------------------------------

_default_limiter: Optional[RateLimiter] = None
_default_lock = threading.Lock()


def _load_config_from_yaml(path: Path) -> RateLimiter:
    import yaml  # type: ignore

    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    defaults = raw.get("defaults", {}) or {}
    policy_default = _Policy(
        rps=float(defaults.get("rps", 8.0)),
        rps_max=float(defaults.get("rps_max", defaults.get("rps", 8.0) * 2)),
        rps_min=float(defaults.get("rps_min", 0.5)),
        burst=float(defaults.get("burst", defaults.get("rps", 8.0))),
        aimd_step=float(defaults.get("aimd_step", 0.5)),
        concurrency=int(defaults.get("concurrency", 8)),
    )
    limiter = RateLimiter(default_policy=policy_default)
    for key, vals in (raw.get("policies") or {}).items():
        vals = vals or {}
        limiter.configure(
            key,
            _Policy(
                rps=float(vals.get("rps", policy_default.rps)),
                rps_max=float(vals.get("rps_max", vals.get("rps", policy_default.rps_max))),
                rps_min=float(vals.get("rps_min", policy_default.rps_min)),
                burst=float(vals.get("burst", vals.get("rps", policy_default.burst))),
                aimd_step=float(vals.get("aimd_step", policy_default.aimd_step)),
                concurrency=int(vals.get("concurrency", policy_default.concurrency)),
            ),
        )
    return limiter


def get_default() -> RateLimiter:
    global _default_limiter
    with _default_lock:
        if _default_limiter is None:
            cfg = Path(__file__).resolve().parents[3] / "inputs" / "rate_limits.yml"
            if cfg.exists():
                try:
                    _default_limiter = _load_config_from_yaml(cfg)
                except Exception as e:
                    logger.warning(f"[ratelimit] failed to load {cfg}: {e}; using defaults")
                    _default_limiter = RateLimiter()
            else:
                _default_limiter = RateLimiter()
        return _default_limiter


def reset_default_for_tests() -> None:
    """Reset the module-level limiter — only call from tests."""
    global _default_limiter
    with _default_lock:
        _default_limiter = None
