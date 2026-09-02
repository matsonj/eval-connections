"""Parsing of the one-shot result strings emitted by :mod:`connections_eval.core`.

``core`` writes a verdict string onto every one-shot completion event, and
several consumers (log renderer, summary extraction) have to read it back:

- ``ONESHOT_SCORE_{score}_GROUPS_{groups}_TRAP_{trap}_MAX_{max}``
- ``ONESHOT_INVALID_MAX_{max}``
- ``ONESHOT_API_ERROR_MAX_{max}``
- ``LINT_RETRY_{rule}`` (a structural re-submission turn, not a graded guess)

Legacy pre-trap runs wrote a bare ``ONESHOT_SCORE_{score}`` with no
``GROUPS_``/``TRAP_``/``MAX_`` tags; those are reported with ``legacy=True`` and
``groups`` inferred as ``min(score, 4)``.

This module is the reference implementation. ``scripts/extract_summaries.py``
re-expresses the same rules as SQL ``regexp_extract`` calls so the aggregation
can run inside DuckDB — keep the two in sync.
"""

import re
from dataclasses import dataclass
from typing import Optional

# Per-puzzle ceiling assumed for legacy verdicts written before MAX_ existed.
DEFAULT_PUZZLE_MAX = 5

SCORE = "score"
INVALID = "invalid"
API_ERROR = "api_error"

_SCORE_RE = re.compile(r"^ONESHOT_SCORE_(\d+)", re.IGNORECASE)
_GROUPS_RE = re.compile(r"_GROUPS_(\d+)", re.IGNORECASE)
_TRAP_RE = re.compile(r"_TRAP_(\d+)", re.IGNORECASE)
_MAX_RE = re.compile(r"_MAX_(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class OneshotResult:
    """A parsed ONESHOT_* verdict."""

    kind: str                       # SCORE | INVALID | API_ERROR
    score: int = 0                  # total points incl. trap bonus
    groups: int = 0                 # groups matched (0-4)
    trap: int = 0                   # trap bonus included in score
    max_score: int = DEFAULT_PUZZLE_MAX  # per-puzzle ceiling
    legacy: bool = False            # bare ONESHOT_SCORE_N, pre-trap scoring

    @property
    def base(self) -> int:
        """Points from matched groups alone (score minus the trap bonus)."""
        return self.score - self.trap


def _int(pattern: "re.Pattern[str]", text: str, default: int) -> int:
    m = pattern.search(text)
    return int(m.group(1)) if m else default


def parse_oneshot_result(result: Optional[str]) -> Optional[OneshotResult]:
    """Parse a one-shot verdict string, or return None if it isn't one."""
    if not result:
        return None
    text = str(result).strip().upper()
    if not text.startswith("ONESHOT"):
        return None

    if text.startswith("ONESHOT_INVALID"):
        return OneshotResult(kind=INVALID, max_score=_int(_MAX_RE, text, DEFAULT_PUZZLE_MAX))
    if text.startswith("ONESHOT_API_ERROR"):
        return OneshotResult(kind=API_ERROR, max_score=_int(_MAX_RE, text, DEFAULT_PUZZLE_MAX))

    m = _SCORE_RE.match(text)
    if not m:
        return None
    score = int(m.group(1))
    groups_match = _GROUPS_RE.search(text)
    return OneshotResult(
        kind=SCORE,
        score=score,
        # Legacy bare ONESHOT_SCORE_N carried no group count; N caps at 4 groups.
        groups=int(groups_match.group(1)) if groups_match else min(score, 4),
        trap=_int(_TRAP_RE, text, 0),
        max_score=_int(_MAX_RE, text, DEFAULT_PUZZLE_MAX),
        legacy=groups_match is None,
    )


def is_lint_retry(result: Optional[str]) -> bool:
    """True for LINT_RETRY_<rule> — a re-submission turn, never a graded guess."""
    return bool(result) and str(result).upper().startswith("LINT_RETRY_")
