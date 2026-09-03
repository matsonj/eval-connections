#!/usr/bin/env python3
"""Generate chat-style HTML logs per run from the MotherDuck controllog tables.

Writes docs/logs/<run_id>.html for every run the leaderboards link to (latest
per model/mode, read from results/run_summaries.csv) plus docs/logs/index.html,
and deletes pages for runs that dropped off that list.

Pages are only rendered when missing; pass --force after a template change.

Environment:
- MOTHERDUCK_DB: MotherDuck database connection string (default: "md:")
"""

import argparse
import csv
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from connections_eval.linter import strip_thinking
from connections_eval.results import API_ERROR, INVALID, parse_oneshot_result
from connections_eval.utils.motherduck import connect_motherduck

DOCS_LOG_DIR = Path("docs/logs")
RUN_SUMMARIES_CSV = Path("results/run_summaries.csv")

PUZZLE_KINDS = ("model_prompt", "model_completion", "model_response",
                "model_response_error", "state_move")


@dataclass
class Posting:
    account_type: str
    unit: str
    delta: float
    dims: Dict[str, Any]


@dataclass
class Event:
    event_id: str
    event_time: str
    dt: datetime
    kind: str
    run_id: str
    actor_task_id: Optional[str]
    payload: Dict[str, Any]
    postings: List[Posting] = field(default_factory=list)


def _as_dict(struct_value: Any) -> Dict[str, Any]:
    """DuckDB STRUCT columns come back as dicts (or NULL)."""
    return struct_value if isinstance(struct_value, dict) else {}


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def load_events_and_postings(run_ids: List[str], db: Optional[str] = None) -> Dict[str, Event]:
    """Load the events/postings of the given runs only.

    The controllog tables hold every run ever recorded, including full request
    and response text, so the run filter is pushed into SQL rather than applied
    after the fact.
    """
    event_by_id: Dict[str, Event] = {}
    if not run_ids:
        return event_by_id

    con = connect_motherduck(db)
    try:
        print("  Loading events...")
        rows = con.execute(
            """
            SELECT event_id, event_time, kind, actor_task_id, run_id, payload_json
            FROM controllog.events
            WHERE run_id IN (SELECT unnest(?::VARCHAR[]))
            ORDER BY event_time
            """,
            [run_ids],
        ).fetchall()

        for event_id, event_time, kind, actor_task_id, run_id, payload_json in rows:
            if not event_id:
                continue
            event_by_id[str(event_id)] = Event(
                event_id=str(event_id),
                event_time=str(event_time),
                dt=_parse_dt(event_time),
                kind=str(kind or ""),
                run_id=str(run_id) if run_id else "",
                actor_task_id=str(actor_task_id) if actor_task_id else None,
                payload=_as_dict(payload_json),
            )
        print(f"  Loaded {len(event_by_id)} events")

        print("  Loading postings...")
        posting_rows = con.execute(
            """
            SELECT p.event_id, p.account_type, p.unit, p.delta_numeric, p.dims_json
            FROM controllog.postings p
            WHERE p.event_id IN (
                SELECT e.event_id FROM controllog.events e
                WHERE e.run_id IN (SELECT unnest(?::VARCHAR[]))
            )
            """,
            [run_ids],
        ).fetchall()

        posting_count = 0
        for event_id, account_type, unit, delta, dims_json in posting_rows:
            ev = event_by_id.get(str(event_id))
            if not ev:
                continue
            ev.postings.append(
                Posting(
                    account_type=str(account_type or ""),
                    unit=str(unit or ""),
                    delta=float(delta or 0),
                    dims=_as_dict(dims_json),
                )
            )
            posting_count += 1
        print(f"  Loaded {posting_count} postings")
    finally:
        con.close()

    return event_by_id


def group_by_run(event_by_id: Dict[str, Event]) -> Dict[str, List[Event]]:
    runs: Dict[str, List[Event]] = {}
    for ev in event_by_id.values():
        if not ev.run_id:
            continue
        runs.setdefault(ev.run_id, []).append(ev)
    for evs in runs.values():
        evs.sort(key=lambda e: e.dt)
    return runs


def summarize_tokens_and_cost(postings: List[Posting]) -> Dict[str, Any]:
    prompt_tokens = 0
    completion_tokens = 0
    money = 0.0
    for p in postings:
        if p.account_type == "resource.tokens" and p.unit == "+tokens" and p.delta > 0:
            # Only count project: side (positive delta); provider: side is negative mirror
            phase = str(p.dims.get("phase", "")).lower()
            if phase == "prompt":
                prompt_tokens += int(p.delta)
            elif phase == "completion":
                completion_tokens += int(p.delta)
        if p.account_type == "resource.money" and p.unit == "$" and p.delta < 0:
            # Cost postings: vendor: side is negative (money leaving); take abs
            money += abs(float(p.delta))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost": money if money > 0 else None,
    }


def escape_html(text: Optional[Any]) -> str:
    return "" if text is None else html.escape(str(text))


def simple_markdown_to_html(text: str) -> str:
    """Convert basic markdown (bold, italic, lists) to HTML after escaping."""
    escaped = escape_html(text)
    # Bold: **text** → <b>text</b>
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)
    # Italic: *text* → <i>text</i>  (but not inside bold)
    escaped = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', escaped)
    # Lines starting with - as list items
    escaped = re.sub(r'^- (.+)$', r'&bull; \1', escaped, flags=re.MULTILINE)
    return escaped


def extract_state_transition(ev: Event) -> Optional[Tuple[str, str, Optional[str]]]:
    """Return (from, to, puzzle_id_str) when a task changes state.

    puzzle_id_str is best-effort parsed from task id or payload.
    """
    pid_str: Optional[str] = None
    # Prefer payload puzzle id if present (e.g., state_move)
    if "puzzle_id" in (ev.payload or {}):
        pid_str = str(ev.payload.get("puzzle_id"))
    acc = ev.actor_task_id or ""
    from_val: Optional[str] = None
    to_val: Optional[str] = None
    for p in ev.postings:
        if p.account_type != "truth.state":
            continue
        if p.dims.get("from") is not None:
            from_val = str(p.dims.get("from"))
        if p.dims.get("to") is not None:
            to_val = str(p.dims.get("to"))
    if from_val or to_val:
        if not pid_str and acc:
            after_t = acc.split(":", 1)[0]
            if after_t.startswith("T"):
                pid_str = after_t[1:]
        return (from_val or "WIP", to_val or "", pid_str)
    return None


_STRAY_TAIL_RE = re.compile(r"\s*</(?:response|speak|output|answer)>\s*$", re.IGNORECASE)
# Content of the blocks linter.strip_thinking removes, so the renderer can show them.
_THINK_BODY_RE = re.compile(r"<think(?:ing)?>(.*?)(?:</think(?:ing)?>|\Z)",
                            re.IGNORECASE | re.DOTALL)


def split_thinking_blocks(text: str) -> Tuple[str, str]:
    """Split response_text into (thinking, rest).

    Primary path: <think>/<thinking> blocks, using the same definition the
    linter strips. Fallback: when the model emits reasoning without the wrapper
    but does include a <guess> block (e.g. granite-4.1-8b after a CORRECT
    verdict), treat everything before the first <guess> as thinking. Trailing
    stray closing tags like </response> or </speak> are stripped from the rest.
    """
    if not text:
        return "", ""
    thinking = "\n\n".join(
        m.group(1).strip() for m in _THINK_BODY_RE.finditer(text) if m.group(1).strip()
    )
    if thinking:
        return thinking, _STRAY_TAIL_RE.sub("", strip_thinking(text).strip())
    guess_idx = text.find("<guess>")
    if guess_idx > 0:
        lead = text[:guess_idx].strip()
        if lead:
            return lead, _STRAY_TAIL_RE.sub("", text[guess_idx:].strip())
    return "", _STRAY_TAIL_RE.sub("", text.strip())


# Shared by the run pages and the index.
BASE_CSS = (
    "body{font-family:ui-monospace,Menlo,Consolas,Monaco,\"Courier New\",monospace;margin:0;padding:0;"
    "background:repeating-linear-gradient(0deg,#e9ecef,#e9ecef 24px,#eff2f5 25px);color:#0f1419;}"
    ".container{max-width:1024px;margin:0 auto;padding:24px;}"
    ".panel{background:#ffffff;border:2px solid #2b3035;box-shadow:inset 0 0 0 1px #d9dde1;}"
    ".topbar{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;"
    "background:#1f2a36;color:#e6eef7;border-bottom:2px solid #2b3035;letter-spacing:0.04em;text-transform:uppercase;}"
    ".title{font-weight:700;} .meta{color:#8aa0b8;font-size:12px;}"
    ".content{padding:16px;}"
    ".footer{color:#48525c;font-size:12px;}"
    "a{color:#0b63ce;text-decoration:none;}a:hover{text-decoration:underline;}"
)

RUN_CSS = BASE_CSS + (
    ".row{display:flex;flex-direction:column;margin:8px 0;}"
    ".bubble{max-width:76%;padding:0;border:2px solid #2b3035;background:#fff;}"
    ".bubble .metahead{background:#f2f5f8;border-bottom:2px solid #2b3035;padding:6px 10px;"
    "font-size:11px;text-transform:uppercase;letter-spacing:0.04em;color:#28323b;display:flex;justify-content:space-between;}"
    ".bubble .body{padding:12px 14px;white-space:pre-wrap;word-wrap:break-word;overflow:hidden;}"
    ".left{align-self:flex-start;border-left:6px solid #6c757d;}"
    ".right{align-self:flex-end;border-left:6px solid #0d6efd;background:#f7fbff;}"
    ".step{border-left:4px solid #2b3035;padding-left:12px;margin:18px 0;}"
    ".state{font-size:12px;color:#34424f;margin-left:2px;margin-top:6px;}"
    ".stats{font-size:12px;color:#1f2a36;margin-top:6px;}"
    ".pill{display:inline-block;background:#f0f3f6;border:2px solid #2b3035;border-radius:0;padding:2px 8px;margin-right:6px;color:#1f2a36;}"
    ".endpill{display:inline-block;background:#e7f5ec;border:2px solid #2b3035;color:#1f3b2a;border-radius:0;padding:4px 10px;margin:10px 0;}"
    "details{margin:8px 0;} details>summary{cursor:pointer;color:#0b63ce;list-style:none;display:flex;align-items:center;}"
    "details>summary::before{content:'\\25B8';font-size:14px;margin-right:6px;flex-shrink:0;transition:transform 0.15s;line-height:1;}"
    "details[open]>summary::before{transform:rotate(90deg);}"
    ".thinking{font-style:italic;font-size:12px;color:#2b333b;white-space:pre-wrap;overflow-x:auto;}"
    ".bubble details{margin:4px 0;}"
    ".footer{margin:24px;}"
    ".puzzle-block{border:2px solid #2b3035;margin:10px 0;background:#fff;}"
    ".puzzle-block>summary{cursor:pointer;padding:10px 14px;display:flex;align-items:center;gap:10px;"
    "background:#f2f5f8;border-bottom:2px solid #2b3035;list-style:none;font-size:13px;}"
    ".puzzle-block>summary::before{content:'\\25B8';font-size:16px;flex-shrink:0;line-height:1;transition:transform 0.15s;}"
    ".puzzle-block[open]>summary::before{transform:rotate(90deg);}"
    ".puzzle-block>summary .puzzle-stats-inline{color:#48525c;font-size:12px;margin-left:auto;text-align:right;white-space:nowrap;}"
    ".puzzle-failed>summary{background:#fff3f3;}"
    ".puzzle-failed>summary .pill{border-color:#c0392b;color:#c0392b;}"
    ".puzzle-solved>summary{background:#eaf6ee;}"
    ".puzzle-solved>summary .pill{border-color:#1f3b2a;color:#1f3b2a;}"
    ".bubble.error{border-left-color:#c0392b;background:#fff5f5;}"
    ".bubble.error .metahead{background:#ffe5e5;color:#7a1f1f;}"
    ".endpill.failed{background:#fde7e9;color:#5b1a1f;}"
)

INDEX_CSS = BASE_CSS + (
    ".table{width:100%;border-collapse:separate;border-spacing:0;}"
    ".thead th{background:#f2f5f8;border:2px solid #2b3035;border-bottom:none;padding:8px 10px;text-align:left;font-size:12px;letter-spacing:.04em;text-transform:uppercase;}"
    ".row{display:grid;grid-template-columns: 48% 32% 20%;align-items:center;}"
    ".tr{border:2px solid #2b3035;border-top:none;background:#fff;}"
    ".td{padding:10px 12px;border-right:2px solid #2b3035;} .td:last-child{border-right:none;}"
    ".footer{margin:16px 0 0 0;}"
)


def _new_puzzle_stats() -> Dict[str, Any]:
    return {"prompt": 0, "completion": 0, "cost": 0.0, "guesses": 0,
            "correct": 0, "start_dt": None}


def _accumulate(ps: Dict[str, Any], tokens: Dict[str, Any], ev: Event,
                result: Optional[str] = None) -> None:
    """Fold one event's tokens/cost (and, for graded turns, its verdict) into
    the puzzle's running stats."""
    ps["prompt"] += int(tokens.get("prompt_tokens") or 0)
    ps["completion"] += int(tokens.get("completion_tokens") or 0)
    ps["cost"] += float(tokens.get("cost") or 0.0)
    if ps["start_dt"] is None:
        ps["start_dt"] = ev.dt

    if result is None:
        return
    res_u = str(result).upper()
    # One graded attempt by the harness per completion. INVALID_RESPONSE (a
    # parse failure) isn't a guess; every real verdict is, even when the
    # renderer can't re-extract guess words from quirky model output.
    if res_u and "INVALID_RESPONSE" not in res_u:
        ps["guesses"] += 1
    oneshot = parse_oneshot_result(res_u)
    if oneshot is not None:
        ps["correct"] += oneshot.groups
    elif "CORRECT" in res_u and "INCORRECT" not in res_u:
        ps["correct"] += 1


def _puzzle_summary(ps: Optional[Dict[str, Any]], end_dt: datetime) -> Dict[str, Any]:
    if not ps:
        return {}
    start_dt = ps.get("start_dt")
    if start_dt and end_dt:
        seconds = int((end_dt - start_dt).total_seconds())
        time_str = f"{seconds // 60:02d}:{seconds % 60:02d}"
    else:
        time_str = "--:--"
    return {
        "prompt_tokens": int(ps.get("prompt", 0)),
        "completion_tokens": int(ps.get("completion", 0)),
        "guesses": int(ps.get("guesses", 0)),
        "correct": int(ps.get("correct", 0)),
        "time": time_str,
        "cost": float(ps.get("cost", 0.0)),
    }


def oneshot_score_breakdown(p_steps: List[Dict[str, Any]]) -> Optional[str]:
    """Human-readable scoring breakdown for a one-shot puzzle, parsed from the
    ONESHOT_* verdict on the puzzle's response step. None for classic puzzles."""
    verdict = None
    for s in p_steps:
        parsed = parse_oneshot_result(s.get("result"))
        if parsed is not None:
            verdict = parsed
    if verdict is None:
        return None
    if verdict.kind == INVALID:
        return f"Invalid · Base: 0 · Bonus: 0 · Total: 0/{verdict.max_score}"
    if verdict.kind == API_ERROR:
        return "API error · Total: 0"
    if verdict.legacy:
        return f"Total: {verdict.score} (legacy scoring)"
    bits = [f"Base: {verdict.base}"]
    if verdict.max_score >= 5:
        bits.append(f"Bonus: {verdict.trap}")
    bits.append(f"Total: {verdict.score}/{verdict.max_score}")
    return " · ".join(bits)


def render_run_html(run_id: str, events: List[Event]) -> str:
    # Determine model/provider from first event with payload
    model = None
    provider = None
    for ev in events:
        model = model or ev.payload.get("model")
        provider = provider or ev.payload.get("provider")
        if model and provider:
            break

    # Build steps grouped by puzzle_id, then ordered by timestamp within each puzzle.
    # This keeps each puzzle's conversation as a coherent block even for parallel runs.
    steps: List[Dict[str, Any]] = []
    # Track last evaluation per puzzle (to show why a puzzle ended)
    last_eval_by_puzzle: Dict[str, Dict[str, Any]] = {}
    puzzle_stats: Dict[str, Dict[str, Any]] = {}

    # Pre-group events by puzzle_id; events without a puzzle_id are dropped.
    puzzle_events: Dict[Any, List[Event]] = {}
    for ev in events:
        pid = ev.payload.get("puzzle_id") if ev.kind in PUZZLE_KINDS else None
        if pid is not None:
            puzzle_events.setdefault(pid, []).append(ev)

    def puzzle_sort_key(pid: Any) -> int:
        try:
            return int(pid)
        except (ValueError, TypeError):
            return 0

    for puzzle_id in sorted(puzzle_events.keys(), key=puzzle_sort_key):
        p_events = sorted(puzzle_events[puzzle_id], key=lambda e: e.dt)
        steps.append({"type": "puzzle_header", "puzzle_id": puzzle_id})
        # Track whether a terminal state transition (WIP→DONE/FAILED/ERROR) was emitted
        # so we can synthesize one for older runs that only logged model_response_error.
        has_terminal_state_move = False
        last_error_event: Optional[Event] = None
        pid = str(puzzle_id)
        for ev in p_events:
            p = ev.payload
            tokens = summarize_tokens_and_cost(ev.postings)
            if ev.kind == "model_prompt":
                steps.append({
                    "type": "prompt",
                    "text": p.get("request_text", ""),
                    "tokens": tokens,
                    "ts": ev.event_time,
                })
                _accumulate(puzzle_stats.setdefault(pid, _new_puzzle_stats()), tokens, ev)
            elif ev.kind == "model_completion":
                steps.append({
                    "type": "response",
                    "text": p.get("response_text", ""),
                    "result": p.get("result"),
                    "wall_ms": p.get("wall_ms"),
                    "puzzle_id": puzzle_id,
                    "tokens": tokens,
                    "ts": ev.event_time,
                })
                _accumulate(puzzle_stats.setdefault(pid, _new_puzzle_stats()), tokens, ev,
                            str(p.get("result", "")))
            elif ev.kind == "model_response":
                last_eval_by_puzzle[pid] = {
                    "guess_index": p.get("guess_index"),
                    "result": p.get("result"),
                    "response_text": p.get("response_text"),
                    "ts": ev.event_time,
                }
                _accumulate(puzzle_stats.setdefault(pid, _new_puzzle_stats()), tokens, ev,
                            str(p.get("result", "")))
                steps.append({
                    "type": "response",
                    "text": p.get("response_text") or p.get("result", ""),
                    "result": p.get("result"),
                    "guess_index": p.get("guess_index"),
                    "puzzle_id": puzzle_id,
                    "tokens": tokens,
                    "ts": ev.event_time,
                })
            elif ev.kind == "model_response_error":
                # Diagnostic event: API call failed after exhausting retries.
                # Render as a response bubble so the failure is visible in the log.
                err_text = p.get("response_text") or p.get("error") or "(API error — no details captured)"
                steps.append({
                    "type": "response",
                    "text": f"⚠ API error: {err_text}",
                    "puzzle_id": puzzle_id,
                    "tokens": tokens,
                    "ts": ev.event_time,
                    "is_error": True,
                })
                last_error_event = ev
            elif ev.kind == "state_move":
                st = extract_state_transition(ev)
                if st:
                    frm, to, pid_str = st
                    puzzle_label = pid_str or pid
                    if str(frm).upper() == "WIP" and str(to).upper() in ("DONE", "FAILED", "ERROR"):
                        has_terminal_state_move = True
                    reason = "SOLVED" if str(to).upper() == "DONE" else ("FAILED" if str(to).upper() == "ERROR" else str(to).upper())
                    steps.append({
                        "type": "task_end",
                        "text": f"{puzzle_label} → {reason}",
                        "from": frm,
                        "to": to,
                        "final_eval": last_eval_by_puzzle.get(str(puzzle_label)) or {},
                        "summary": _puzzle_summary(puzzle_stats.get(str(puzzle_label)), ev.dt),
                        "ts": ev.event_time,
                    })

        # Backfill: older runs logged WIP→FAILED via postings on model_response_error
        # without a separate state_move event, leaving puzzles visually stuck in WIP.
        # Synthesize a terminal task_end so the renderer reflects the actual outcome.
        if not has_terminal_state_move and last_error_event is not None:
            steps.append({
                "type": "task_end",
                "text": f"{puzzle_id} → FAILED",
                "from": "WIP",
                "to": "FAILED",
                "final_eval": last_eval_by_puzzle.get(pid) or {},
                "summary": _puzzle_summary(puzzle_stats.get(pid), last_error_event.dt),
                "ts": last_error_event.event_time,
            })

    # Render HTML
    parts: List[str] = []
    parts.append("<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">")
    parts.append(f"<title>Run {escape_html(run_id)} · Logs</title>")
    parts.append(f"<style>{RUN_CSS}</style></head><body>")
    parts.append("<div class=\"container\">")
    parts.append("<div class=\"panel\">")
    parts.append("<div class=\"topbar\">")
    parts.append(f"<div class=\"title\">Run {escape_html(run_id)}</div>")
    parts.append(f"<div class=\"meta\">{escape_html(provider or '')} · {escape_html(model or '')} · <a href=\"../index.html\" style=\"color:#b6c7da\">Back</a></div>")
    parts.append("</div>")
    parts.append("<div class=\"content\">")

    # Group steps by puzzle for collapsible rendering
    puzzle_step_groups: List[Tuple[Optional[Any], List[Dict[str, Any]]]] = []
    current_group_pid: Optional[Any] = None
    current_group_steps: List[Dict[str, Any]] = []
    for step in steps:
        if step.get("type") == "puzzle_header":
            if current_group_steps:
                puzzle_step_groups.append((current_group_pid, current_group_steps))
            current_group_pid = step.get("puzzle_id")
            current_group_steps = []
        else:
            current_group_steps.append(step)
    if current_group_steps:
        puzzle_step_groups.append((current_group_pid, current_group_steps))

    # Sort puzzles: lowest correct rate first, tie-break by highest cost first
    def puzzle_audit_sort_key(item: Tuple[Optional[Any], List[Dict[str, Any]]]) -> Tuple[float, float]:
        pid = item[0]
        ps = puzzle_stats.get(str(pid), {}) if pid is not None else {}
        guesses = int(ps.get("guesses", 0))
        rate = int(ps.get("correct", 0)) / guesses if guesses > 0 else 0.0
        return (rate, -float(ps.get("cost", 0.0)))  # ascending rate, descending cost

    puzzle_step_groups.sort(key=puzzle_audit_sort_key)

    for puzzle_pid, p_steps in puzzle_step_groups:
        # Build summary stats for the collapsed header
        ps = puzzle_stats.get(str(puzzle_pid), {}) if puzzle_pid is not None else {}
        correct = int(ps.get("correct", 0))
        guesses = int(ps.get("guesses", 0))
        pct = f"{correct / guesses * 100:.0f}%" if guesses > 0 else "—"
        cost = float(ps.get("cost", 0.0))
        # Pick header style from the actual terminal outcome (last task_end step), not
        # from "any wrong guesses ever" — a solved puzzle that took mistakes en route
        # should still read as solved.
        terminal_to: Optional[str] = None
        for s in reversed(p_steps):
            if s.get("type") == "task_end":
                terminal_to = str(s.get("to", "")).upper()
                break
        if terminal_to == "DONE":
            outcome_class = " puzzle-solved"
        elif terminal_to in ("FAILED", "ERROR"):
            outcome_class = " puzzle-failed"
        else:
            outcome_class = ""

        # One-shot puzzles show the scoring breakdown in the header; classic
        # puzzles keep the correct/guesses ratio (which is meaningless for
        # one-shot: groups-matched over a single completion reads as "4/1").
        breakdown = oneshot_score_breakdown(p_steps)
        if breakdown:
            # One-shot inference time comes from the completion's wall_ms —
            # state-move timestamps all land post-response and read as 0s.
            wall_ms = 0
            for s in p_steps:
                try:
                    wall_ms += int(s.get("wall_ms") or 0)
                except (TypeError, ValueError):
                    pass
            stats_inline = f"{breakdown} · Cost: ${cost:0.4f}"
            if wall_ms:
                stats_inline += f" · Time: {wall_ms / 1000:.1f}s"
        else:
            stats_inline = f"{correct}/{guesses} correct ({pct}) · ${cost:0.4f}"

        parts.append(f"<details class=\"puzzle-block{outcome_class}\">")
        parts.append(
            f"<summary class=\"puzzle-summary\">"
            f"<span class=\"pill\">Puzzle {escape_html(puzzle_pid)}</span>"
            f"<span class=\"puzzle-stats-inline\">"
            f"{escape_html(stats_inline)}"
            f"</span>"
            f"</summary>"
        )

        for step in p_steps:
            st = step.get("type")
            if st == "state":
                parts.append(f"<div class=\"state\">{escape_html(step.get('text'))}</div>")
                continue
            if st == "task_end":
                to_state = str(step.get("to", "")).upper()
                pill_class = "endpill failed" if to_state in ("FAILED", "ERROR") else "endpill"
                parts.append(f"<div class=\"{pill_class}\">{escape_html(step.get('text'))} <span class=\"meta\">({escape_html(step.get('from',''))} → {escape_html(step.get('to',''))})</span></div>")
                fe = step.get("final_eval", {})
                if fe:
                    gi = fe.get("guess_index")
                    res = fe.get("result")
                    rtxt = fe.get("response_text")
                    details_bits = []
                    if res:
                        details_bits.append(f"Result: {escape_html(res)}")
                    if gi is not None:
                        details_bits.append(f"Guess #: {escape_html(gi)}")
                    if rtxt:
                        th, rr = split_thinking_blocks(rtxt)
                        inner = []
                        if th:
                            inner.append(f"<details><summary>Show final thinking</summary><div class=\"thinking\">{simple_markdown_to_html(th)}</div></details>")
                        body = rr if (rr or th) else rtxt
                        if body:
                            inner.append(f"<div>{simple_markdown_to_html(body)}</div>")
                        details_bits.append(" ".join(inner))
                    parts.append(f"<div class=\"stats\">{' · '.join(details_bits)}</div>")
                summ = step.get("summary", {})
                if summ:
                    parts.append(
                        "<div class=\"stats\">"
                        f"prompt: {summ.get('prompt_tokens', 0):,} · completion: {summ.get('completion_tokens', 0):,}"
                        f" · guesses: {summ.get('guesses', 0)} · correct: {summ.get('correct', 0)}"
                        f" · time: {summ.get('time', '--:--')} · cost: ${summ.get('cost', 0.0):0.4f}"
                        "</div>"
                    )
                continue
            if st == "prompt":
                parts.append("<div class=\"row\">")
                parts.append("<div class=\"bubble left\">")
                parts.append(f"<div class=\"metahead\"><span>PROMPT</span><span>{escape_html(step.get('ts',''))}</span></div>")
                parts.append(f"<div class=\"body\">{escape_html(step.get('text', ''))}</div>")
                parts.append("</div>")
                parts.append(_stats_line(step.get("tokens", {})))
                parts.append("</div>")
                continue
            if st == "response":
                raw_text = step.get("text", "")
                thinking, rest = split_thinking_blocks(raw_text)
                parts.append("<div class=\"row\">")
                bubble_inner: List[str] = []
                if thinking:
                    bubble_inner.append(
                        f"<details><summary>Show thinking</summary><div class=\"thinking\">{simple_markdown_to_html(thinking)}</div></details>"
                    )
                # Only fall back to the raw text when nothing was split out of it;
                # a response that was entirely thinking renders as the block above.
                body = rest if (rest or thinking) else raw_text
                if body:
                    bubble_inner.append(f"<div>{simple_markdown_to_html(body)}</div>")
                bubble_class = "bubble right error" if step.get("is_error") else "bubble right"
                parts.append(f"<div class=\"{bubble_class}\">")
                gh = []
                if step.get("guess_index") is not None:
                    gh.append(f"Guess {escape_html(step['guess_index'])}")
                if step.get("result"):
                    gh.append(escape_html(step.get("result")))
                if step.get("is_error"):
                    meta_right = "API ERROR"
                else:
                    meta_right = " · ".join(gh) if gh else "RESPONSE"
                parts.append(f"<div class=\"metahead\"><span>{meta_right}</span><span>{escape_html(step.get('ts',''))}</span></div>")
                parts.append(f"<div class=\"body\">{''.join(bubble_inner)}</div>")
                parts.append("</div>")
                parts.append(_stats_line(step.get("tokens", {}), step.get("result")))
                parts.append("</div>")
                continue

        parts.append("</details>")

    parts.append("<div class=\"footer\">Generated by generate_logs_view.py</div>")
    parts.append("</div></div></div></body></html>")
    return "".join(parts)


def _stats_line(tok: Dict[str, Any], result: Optional[str] = None) -> str:
    stats = []
    if tok.get("prompt_tokens"):
        stats.append(f"prompt: {tok['prompt_tokens']:,}")
    if tok.get("completion_tokens"):
        stats.append(f"completion: {tok['completion_tokens']:,}")
    if tok.get("cost"):
        stats.append(f"cost: ${tok['cost']:.6f}")
    if result:
        stats.append(f"result: {escape_html(result)}")
    return f"<div class=\"stats\">{' · '.join(stats)}</div>" if stats else ""


def write_run_page(run_id: str, html_text: str) -> Path:
    DOCS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = DOCS_LOG_DIR / f"{run_id}.html"
    out.write_text(html_text, encoding="utf-8")
    return out


def build_logs_index(pages: List[Tuple[str, Path]]) -> None:
    # Industrial 1980s-themed index. Run ids are "<timestamp>_<model>".
    rows_html = []
    for run, p in sorted(pages):
        model = run.split("_", 1)[1] if "_" in run else ""
        rows_html.append(
            "".join(
                [
                    "<div class=\"tr row\">",
                    f"<div class=\"td\">{escape_html(run)}</div>",
                    f"<div class=\"td\">{escape_html(model)}</div>",
                    f"<div class=\"td link\"><a href=\"{escape_html(p.name)}\">Open</a></div>",
                    "</div>",
                ]
            )
        )

    page = (
        "<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Logs</title>" f"<style>{INDEX_CSS}</style></head><body>"
        "<div class=\"container\"><div class=\"panel\">"
        "<div class=\"topbar\"><div class=\"title\">Logs</div>"
        "<div class=\"meta\"><a href=\"../index.html\" style=\"color:#b6c7da\">Back</a></div></div>"
        "<div class=\"content\">"
        "<div class=\"thead\"><div class=\"row\">"
        "<div class=\"td\" style=\"border:2px solid #2b3035;border-bottom:none;background:#f2f5f8;\">Run ID</div>"
        "<div class=\"td\" style=\"border:2px solid #2b3035;border-bottom:none;background:#f2f5f8;\">Model</div>"
        "<div class=\"td\" style=\"border:2px solid #2b3035;border-bottom:none;background:#f2f5f8;\">Action</div>"
        "</div></div>"
        + "".join(rows_html) +
        "<div class=\"footer\">Generated by generate_logs_view.py</div>"
        "</div></div></div></body></html>"
    )

    (DOCS_LOG_DIR / "index.html").write_text(page, encoding="utf-8")


def read_allowed_run_ids() -> List[str]:
    """Run ids worth a page: the latest run per (model, mode) among rows with a
    real puzzle count and a recorded cost. Deliberately looser than the
    leaderboard filter in create_results_mviz.py so a run that just misses the
    board still has a transcript to link to."""
    if not RUN_SUMMARIES_CSV.exists():
        return []

    rows: List[Dict[str, Any]] = []
    with RUN_SUMMARIES_CSV.open("r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                if int(r.get("puzzles_attempted", "0") or 0) < 11:
                    continue
                # One-shot runs have exactly one guess per puzzle (<= 20 on the
                # canonical set), so the classic >40 guess floor would exclude
                # every one of them. Only apply it to classic runs.
                run_mode = (r.get("mode") or "classic").strip() or "classic"
                if run_mode != "oneshot" and int(r.get("total_guesses", "0") or 0) <= 40:
                    continue
                if r.get("total_cost", "") in ("", None):
                    continue
                rows.append(r)
            except (TypeError, ValueError):
                continue

    # The one-shot and classic leaderboards each link their own latest run per model.
    best_by_key: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        key = f"{r.get('model', '')}|{(r.get('mode') or 'classic').strip() or 'classic'}"
        # MotherDuck emits some timestamps tz-naive; normalize to UTC so
        # comparisons don't mix offset-aware and offset-naive datetimes.
        dt = _parse_dt(r.get("start_timestamp", ""))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        existing = best_by_key.get(key)
        if existing is None or dt > existing["dt"]:
            best_by_key[key] = {"row": r, "dt": dt}

    return [v["row"]["run_id"] for v in best_by_key.values() if v["row"].get("run_id")]


def prune_orphan_pages(allowed: Iterable[str]) -> int:
    """Delete run pages whose run is no longer on either leaderboard."""
    keep = set(allowed)
    removed = 0
    for page in DOCS_LOG_DIR.glob("*.html"):
        if page.name == "index.html" or page.stem in keep:
            continue
        page.unlink()
        removed += 1
    if removed:
        print(f"🧹 Pruned {removed} orphaned log page(s)")
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="Re-render every run page, not just missing ones "
                             "(use after a template/CSS change)")
    args = parser.parse_args()

    allowed_run_ids = read_allowed_run_ids()
    print(f"📋 {len(allowed_run_ids)} runs linked from the leaderboards")
    if not allowed_run_ids:
        print("No run_summaries.csv rows to render; nothing to do")
        return

    prune_orphan_pages(allowed_run_ids)

    if args.force:
        to_render = list(allowed_run_ids)
    else:
        to_render = [r for r in allowed_run_ids if not (DOCS_LOG_DIR / f"{r}.html").exists()]
    print(f"🧭 Rendering {len(to_render)} run page(s)"
          + ("" if args.force else f" ({len(allowed_run_ids) - len(to_render)} already present)"))

    pages: List[Tuple[str, Path]] = [
        (r, DOCS_LOG_DIR / f"{r}.html")
        for r in allowed_run_ids
        if r not in to_render
    ]

    if to_render:
        runs = group_by_run(load_events_and_postings(to_render))
        for run_id in sorted(to_render):
            evs = runs.get(run_id)
            if not evs:
                print(f"  !! No controllog events for {run_id}; skipping")
                continue
            out = write_run_page(run_id, render_run_html(run_id, evs))
            pages.append((run_id, out))
            print(f"  Wrote {out}")

    build_logs_index(pages)
    print(f"✅ Logs index written to {DOCS_LOG_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
