import json
import os
import time
import uuid
from dataclasses import dataclass, field
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# -------------------------
# Configuration and globals
# -------------------------


@dataclass
class SDKConfig:
    project_id: str
    log_dir: Path
    default_dims: Dict[str, Any] = field(default_factory=dict)
    pii_scrub: bool = False  # keep raw payloads (user requested)


_config: Optional[SDKConfig] = None


def init(
    project_id: str,
    log_dir: Path,
    default_dims: Optional[Dict[str, Any]] = None,
) -> None:
    """Initialize controllog SDK for JSONL transport.

    Args:
        project_id: Logical project identifier.
        log_dir: Base directory where JSONL logs will be written.
        default_dims: Default dimensions to add to every event/posting.
    """
    global _config

    log_dir = Path(log_dir)
    # Partition by date under "controllog" subdir
    log_dir.mkdir(parents=True, exist_ok=True)
    _config = SDKConfig(
        project_id=project_id,
        log_dir=log_dir,
        default_dims=default_dims or {},
        pii_scrub=False,
    )


# -------------------------
# JSONL transport helpers
# -------------------------


def _date_partition_dir(base: Path) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    part = base / "controllog" / today
    part.mkdir(parents=True, exist_ok=True)
    return part


def _events_file() -> Path:
    assert _config is not None, "controllog.init() must be called before use"
    return _date_partition_dir(_config.log_dir) / "events.jsonl"


def _postings_file() -> Path:
    assert _config is not None, "controllog.init() must be called before use"
    return _date_partition_dir(_config.log_dir) / "postings.jsonl"


def _write_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


# -------------------------
# Core data constructors
# -------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid7_str() -> str:
    """Generate a UUIDv7 string (sortable by time) without relying on stdlib uuid7.

    Layout (per draft RFC 4122 v7):
      - 48 bits: unix time in milliseconds
      - 4 bits: version (0b0111)
      - 12 bits: random
      - 2 bits: variant (0b10)
      - 62 bits: random
    """
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big")  # 64 bits

    b = bytearray(16)
    # 48-bit timestamp big-endian
    b[0:6] = ts_ms.to_bytes(6, "big")
    # version (0x7) in high nibble of byte 6, top 4 bits of rand_a in low nibble
    b[6] = (0x70 | ((rand_a >> 8) & 0x0F))
    b[7] = rand_a & 0xFF
    # variant '10' in top two bits of byte 8, then top 6 bits of rand_b
    b[8] = 0x80 | ((rand_b >> 56) & 0x3F)
    # remaining 56 bits of rand_b into bytes 9..15
    lower_56 = rand_b & ((1 << 56) - 1)
    for i in range(7):
        shift = (6 - i) * 8
        b[9 + i] = (lower_56 >> shift) & 0xFF

    return str(uuid.UUID(bytes=bytes(b)))


def new_id() -> str:
    """Public UUIDv7 generator for correlation (e.g., exchange_id)."""
    return _uuid7_str()


def post(
    account_type: str,
    account_id: str,
    unit: str,
    delta: float,
    dims: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a posting line (not yet persisted).

    Returns a plain dict; the caller passes the collection to event().
    """
    posting = {
        "posting_id": _uuid7_str(),
        "event_id": None,  # filled during event()
        "account_type": account_type,
        "account_id": account_id,
        "unit": unit,
        "delta_numeric": float(delta),
        "dims_json": dims or {},
    }
    return posting


def _check_invariants(kind: str, postings: List[Dict[str, Any]]) -> None:
    """Enforce minimal double-entry invariants at write-time.

    Rules implemented (per event):
      - For account_types in {resource.tokens, resource.money, resource.time_ms, value.utility, truth.state},
        sum(delta_numeric) per (account_type, unit) must be zero within reasonable epsilon.
    """
    if not postings:
        return

    sums: Dict[tuple, float] = {}
    for p in postings:
        key = (p["account_type"], p["unit"])
        sums[key] = sums.get(key, 0.0) + float(p["delta_numeric"])

    epsilon = 1e-9
    for (acct, unit), total in sums.items():
        if acct.startswith("resource.") or acct in ("value.utility", "truth.state"):
            if abs(total) > epsilon:
                raise ValueError(
                    f"UNBALANCED_POSTINGS: account_type={acct}, unit={unit}, net={total} for event kind={kind}"
                )


def event(
    *,
    kind: str,
    actor: Optional[Dict[str, str]] = None,
    run_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    postings: Optional[List[Dict[str, Any]]] = None,
    project_id: Optional[str] = None,
    source: str = "sdk",
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Emit a structured event and balanced postings to JSONL.

    Returns the persisted event dict.
    """
    assert _config is not None, "controllog.init() must be called before use"

    event_id = _uuid7_str()
    event_time = _now_iso()

    # Fill defaults
    actor = actor or {}
    payload = payload or {}
    postings = postings or []
    project = project_id or _config.project_id

    # Invariant checks
    _check_invariants(kind, postings)

    # Persist event
    event_row = {
        "event_id": event_id,
        "event_time": event_time,
        "ingest_time": _now_iso(),
        "kind": kind,
        "actor_agent_id": actor.get("agent_id"),
        "actor_task_id": actor.get("task_id"),
        "project_id": project,
        "run_id": run_id,
        "source": source,
        "idempotency_key": idempotency_key or event_id,
        "payload_json": {**payload},
    }

    # Write event
    _write_jsonl(_events_file(), {**_config.default_dims, **event_row})

    # Persist postings (attach event_id)
    for p in postings:
        p_out = dict(p)
        p_out["event_id"] = event_id
        _write_jsonl(_postings_file(), {**_config.default_dims, **p_out})

    return event_row



