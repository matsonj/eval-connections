"""Controllog JSONL transport.

Events and postings form a simple double-entry ledger: every event may carry
a set of postings, and for the tracked account types the postings on an
event must net to zero (see `_check_invariants`). This keeps resource and
state changes auditable without a database.
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SDKConfig:
    project_id: str
    log_dir: Path


_config: Optional[SDKConfig] = None

_write_lock = threading.Lock()
_partition_cache: Dict[str, Path] = {}


def init(project_id: str, log_dir: Path) -> None:
    """Initialize controllog SDK for JSONL transport.

    Args:
        project_id: Logical project identifier.
        log_dir: Base directory where JSONL logs will be written.
    """
    global _config

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    _config = SDKConfig(project_id=project_id, log_dir=log_dir)
    _partition_cache.clear()


def _date_partition_dir(base: Path) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cached = _partition_cache.get(today)
    if cached is not None:
        return cached
    part = base / "controllog" / today
    part.mkdir(parents=True, exist_ok=True)
    _partition_cache[today] = part
    return part


def _events_file() -> Path:
    assert _config is not None, "controllog.init() must be called before use"
    return _date_partition_dir(_config.log_dir) / "events.jsonl"


def _postings_file() -> Path:
    assert _config is not None, "controllog.init() must be called before use"
    return _date_partition_dir(_config.log_dir) / "postings.jsonl"


def _append_lines(path: Path, lines: List[str]) -> None:
    if not lines:
        return
    blob = "\n".join(lines) + "\n"
    with _write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(blob)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid7_str() -> str:
    """Generate a UUIDv7 string (sortable by time); stdlib has no uuid7 on 3.12."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big")  # 64 bits

    b = bytearray(16)
    b[0:6] = ts_ms.to_bytes(6, "big")
    b[6] = 0x70 | ((rand_a >> 8) & 0x0F)
    b[7] = rand_a & 0xFF
    b[8] = 0x80 | ((rand_b >> 56) & 0x3F)
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
    """Create a posting line (not yet persisted); pass the collection to event()."""
    return {
        "posting_id": _uuid7_str(),
        "event_id": None,  # filled during event()
        "account_type": account_type,
        "account_id": account_id,
        "unit": unit,
        "delta_numeric": float(delta),
        "dims_json": dims or {},
    }


def _check_invariants(kind: str, postings: List[Dict[str, Any]]) -> None:
    """Enforce double-entry balance per event for tracked account types.

    For account_types in {resource.*, value.utility, truth.state}, the sum of
    delta_numeric per (account_type, unit) must be zero within epsilon.
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
    """Emit a structured event and its balanced postings to JSONL.

    Returns the persisted event dict.
    """
    assert _config is not None, "controllog.init() must be called before use"

    event_id = _uuid7_str()
    now = _now_iso()

    actor = actor or {}
    payload = payload or {}
    postings = postings or []
    project = project_id or _config.project_id

    _check_invariants(kind, postings)

    event_row = {
        "event_id": event_id,
        "event_time": now,
        "ingest_time": now,
        "kind": kind,
        "actor_agent_id": actor.get("agent_id"),
        "actor_task_id": actor.get("task_id"),
        "project_id": project,
        "run_id": run_id,
        "source": source,
        "idempotency_key": idempotency_key or event_id,
        "payload_json": {**payload},
    }

    _append_lines(_events_file(), [json.dumps(event_row, ensure_ascii=False)])

    if postings:
        posting_lines = []
        for p in postings:
            p_out = dict(p)
            p_out["event_id"] = event_id
            posting_lines.append(json.dumps(p_out, ensure_ascii=False))
        _append_lines(_postings_file(), posting_lines)

    return event_row
