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
from string import Template
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


def _int0(value: Any) -> int:
    """int(value), with anything unparseable counting as 0."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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

        for eid, etime, kind, task_id, run, payload in rows:
            if not eid:
                continue
            event_by_id[str(eid)] = Event(
                event_id=str(eid), event_time=str(etime), dt=_parse_dt(etime),
                kind=str(kind or ""), run_id=str(run) if run else "",
                actor_task_id=str(task_id) if task_id else None,
                payload=_as_dict(payload))
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
        for eid, account_type, unit, delta, dims in posting_rows:
            ev = event_by_id.get(str(eid))
            if not ev:
                continue
            ev.postings.append(Posting(str(account_type or ""), str(unit or ""),
                                       float(delta or 0), _as_dict(dims)))
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
    # Tokens: only count the project: side (positive delta); the provider: side is
    # a negative mirror. Cost: the vendor: side is negative (money leaving), so abs.
    phases = {"prompt": 0, "completion": 0}
    money = 0.0
    for p in postings:
        if p.account_type == "resource.tokens" and p.unit == "+tokens" and p.delta > 0:
            phase = str(p.dims.get("phase", "")).lower()
            if phase in phases:
                phases[phase] += int(p.delta)
        elif p.account_type == "resource.money" and p.unit == "$" and p.delta < 0:
            money += abs(float(p.delta))
    return {"prompt_tokens": phases["prompt"], "completion_tokens": phases["completion"],
            "cost": money if money > 0 else None}


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
    # Prefer payload puzzle id if present (e.g., state_move)
    pid_str: Optional[str] = (str(ev.payload.get("puzzle_id"))
                              if "puzzle_id" in (ev.payload or {}) else None)
    from_val: Optional[str] = None
    to_val: Optional[str] = None
    for p in ev.postings:
        if p.account_type != "truth.state":
            continue
        from_val = str(p.dims["from"]) if p.dims.get("from") is not None else from_val
        to_val = str(p.dims["to"]) if p.dims.get("to") is not None else to_val
    if not (from_val or to_val):
        return None
    acc = ev.actor_task_id or ""
    if not pid_str and acc.startswith("T"):
        pid_str = acc.split(":", 1)[0][1:]
    return (from_val or "WIP", to_val or "", pid_str)


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


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------

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

# Run pages and the index both style `.row`, but as a flex column and as a grid
# respectively, so the two extensions must stay in separate <style> blocks.
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

BACK_LINK = "<a href=\"../index.html\" style=\"color:#b6c7da\">Back</a>"
TH_STYLE = "border:2px solid #2b3035;border-bottom:none;background:#f2f5f8;"

# Both pages are the same chrome around a $content block.
PAGE = Template(
    "<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>$title</title><style>$css</style></head><body>"
    "<div class=\"container\"><div class=\"panel\"><div class=\"topbar\">"
    "<div class=\"title\">$heading</div><div class=\"meta\">$meta</div></div>"
    "<div class=\"content\">$content"
    "<div class=\"footer\">Generated by generate_logs_view.py</div>"
    "</div></div></div></body></html>"
)


@dataclass
class PuzzleStats:
    """Running tokens/cost/verdict totals for one puzzle."""

    prompt: int = 0
    completion: int = 0
    cost: float = 0.0
    guesses: int = 0
    correct: int = 0
    start_dt: Optional[datetime] = None

    def add(self, tokens: Dict[str, Any], dt: datetime, result: Optional[str] = None) -> None:
        """Fold one event's tokens/cost (and, for graded turns, its verdict) in."""
        self.prompt += _int0(tokens.get("prompt_tokens"))
        self.completion += _int0(tokens.get("completion_tokens"))
        self.cost += float(tokens.get("cost") or 0.0)
        if self.start_dt is None:
            self.start_dt = dt

        if result is None:
            return
        res_u = str(result).upper()
        # One graded attempt by the harness per completion. INVALID_RESPONSE (a
        # parse failure) isn't a guess; every real verdict is, even when the
        # renderer can't re-extract guess words from quirky model output.
        if res_u and "INVALID_RESPONSE" not in res_u:
            self.guesses += 1
        oneshot = parse_oneshot_result(res_u)
        if oneshot is not None:
            self.correct += oneshot.groups
        elif "CORRECT" in res_u and "INCORRECT" not in res_u:
            self.correct += 1

    @property
    def audit_key(self) -> Tuple[float, float]:
        """Sort key: lowest correct rate first, tie-break by highest cost first."""
        rate = self.correct / self.guesses if self.guesses else 0.0
        return (rate, -self.cost)

    def summary_line(self, end_dt: Optional[datetime]) -> str:
        if self.start_dt and end_dt:
            seconds = int((end_dt - self.start_dt).total_seconds())
            elapsed = f"{seconds // 60:02d}:{seconds % 60:02d}"
        else:
            elapsed = "--:--"
        return (f"prompt: {self.prompt:,} · completion: {self.completion:,}"
                f" · guesses: {self.guesses} · correct: {self.correct}"
                f" · time: {elapsed} · cost: ${self.cost:0.4f}")


NO_STATS = PuzzleStats()  # stand-in for puzzles that never accumulated anything


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


def _thinking_html(raw: str, label: str, sep: str = "") -> str:
    """A collapsed thinking block (when there is one) plus the remaining body."""
    thinking, rest = split_thinking_blocks(raw)
    out: List[str] = []
    if thinking:
        out.append(f"<details><summary>{label}</summary>"
                   f"<div class=\"thinking\">{simple_markdown_to_html(thinking)}</div></details>")
    # Only fall back to the raw text when nothing was split out of it; a response
    # that was entirely thinking renders as the block above.
    body = rest if (rest or thinking) else raw
    if body:
        out.append(f"<div>{simple_markdown_to_html(body)}</div>")
    return sep.join(out)


def _bubble(css_class: str, meta_left: str, step: Dict[str, Any], body: str,
            result: Optional[str] = None) -> str:
    """One chat bubble plus its trailing token/cost line."""
    return (
        f"<div class=\"row\"><div class=\"{css_class}\">"
        f"<div class=\"metahead\"><span>{meta_left}</span>"
        f"<span>{escape_html(step.get('ts', ''))}</span></div>"
        f"<div class=\"body\">{body}</div></div>"
        f"{_stats_line(step.get('tokens', {}), result)}</div>"
    )


def _render_task_end(step: Dict[str, Any]) -> str:
    to_state = str(step.get("to", "")).upper()
    pill_class = "endpill failed" if to_state in ("FAILED", "ERROR") else "endpill"
    out = [f"<div class=\"{pill_class}\">{escape_html(step.get('text'))} "
           f"<span class=\"meta\">({escape_html(step.get('from', ''))} → "
           f"{escape_html(step.get('to', ''))})</span></div>"]
    fe = step.get("final_eval", {})
    if fe:
        bits = []
        if fe.get("result"):
            bits.append(f"Result: {escape_html(fe['result'])}")
        if fe.get("guess_index") is not None:
            bits.append(f"Guess #: {escape_html(fe['guess_index'])}")
        if fe.get("response_text"):
            bits.append(_thinking_html(fe["response_text"], "Show final thinking", " "))
        out.append(f"<div class=\"stats\">{' · '.join(bits)}</div>")
    if step.get("summary"):
        out.append(f"<div class=\"stats\">{step['summary']}</div>")
    return "".join(out)


def _render_response(step: Dict[str, Any]) -> str:
    if step.get("is_error"):
        meta_left = "API ERROR"
    else:
        head = []
        if step.get("guess_index") is not None:
            head.append(f"Guess {escape_html(step['guess_index'])}")
        if step.get("result"):
            head.append(escape_html(step.get("result")))
        meta_left = " · ".join(head) if head else "RESPONSE"
    css_class = "bubble right error" if step.get("is_error") else "bubble right"
    body = _thinking_html(step.get("text", ""), "Show thinking")
    return _bubble(css_class, meta_left, step, body, step.get("result"))


def _puzzle_steps(p_events: List[Event], pid: str,
                  stats_by_puzzle: Dict[str, PuzzleStats],
                  eval_by_puzzle: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Turn one puzzle's events into render steps, accumulating its stats."""
    steps: List[Dict[str, Any]] = []
    # Track whether a terminal state transition (WIP→DONE/FAILED/ERROR) was emitted
    # so we can synthesize one for older runs that only logged model_response_error.
    has_terminal_state_move = False
    last_error_event: Optional[Event] = None

    def account(tokens: Dict[str, Any], ev: Event, result: Optional[str] = None) -> None:
        stats_by_puzzle.setdefault(pid, PuzzleStats()).add(tokens, ev.dt, result)

    for ev in p_events:
        p = ev.payload
        tokens = summarize_tokens_and_cost(ev.postings)
        common = {"puzzle_id": ev.payload.get("puzzle_id"), "tokens": tokens,
                  "ts": ev.event_time}
        if ev.kind == "model_prompt":
            steps.append({"type": "prompt", "text": p.get("request_text", ""),
                          "tokens": tokens, "ts": ev.event_time})
            account(tokens, ev)
        elif ev.kind == "model_completion":
            steps.append({"type": "response", "text": p.get("response_text", ""),
                          "result": p.get("result"), "wall_ms": p.get("wall_ms"), **common})
            account(tokens, ev, str(p.get("result", "")))
        elif ev.kind == "model_response":
            eval_by_puzzle[pid] = {"guess_index": p.get("guess_index"), "result": p.get("result"),
                                   "response_text": p.get("response_text"), "ts": ev.event_time}
            account(tokens, ev, str(p.get("result", "")))
            steps.append({"type": "response", "text": p.get("response_text") or p.get("result", ""),
                          "result": p.get("result"), "guess_index": p.get("guess_index"), **common})
        elif ev.kind == "model_response_error":
            # Diagnostic event: API call failed after exhausting retries.
            # Render as a response bubble so the failure is visible in the log.
            err = p.get("response_text") or p.get("error") or "(API error — no details captured)"
            steps.append({"type": "response", "text": f"⚠ API error: {err}",
                          "is_error": True, **common})
            last_error_event = ev
        elif ev.kind == "state_move":
            st = extract_state_transition(ev)
            if not st:
                continue
            frm, to, pid_str = st
            label = pid_str or pid
            if str(frm).upper() == "WIP" and str(to).upper() in ("DONE", "FAILED", "ERROR"):
                has_terminal_state_move = True
            to_u = str(to).upper()
            reason = "SOLVED" if to_u == "DONE" else ("FAILED" if to_u == "ERROR" else to_u)
            steps.append(_task_end(f"{label} → {reason}", frm, to, ev.event_time,
                                   eval_by_puzzle.get(str(label)) or {},
                                   stats_by_puzzle.get(str(label)), ev.dt))

    # Backfill: older runs logged WIP→FAILED via postings on model_response_error
    # without a separate state_move event, leaving puzzles visually stuck in WIP.
    # Synthesize a terminal task_end so the renderer reflects the actual outcome.
    if not has_terminal_state_move and last_error_event is not None:
        steps.append(_task_end(f"{pid} → FAILED", "WIP", "FAILED", last_error_event.event_time,
                               eval_by_puzzle.get(pid) or {}, stats_by_puzzle.get(pid),
                               last_error_event.dt))
    return steps


def _task_end(text: str, frm: str, to: str, ts: str, final_eval: Dict[str, Any],
              stats: Optional[PuzzleStats], end_dt: datetime) -> Dict[str, Any]:
    return {"type": "task_end", "text": text, "from": frm, "to": to, "ts": ts,
            "final_eval": final_eval,
            "summary": stats.summary_line(end_dt) if stats is not None else ""}


# Terminal state → puzzle-header modifier class.
OUTCOME_CLASS = {"DONE": " puzzle-solved", "FAILED": " puzzle-failed",
                 "ERROR": " puzzle-failed"}


def _puzzle_header(pid: Any, p_steps: List[Dict[str, Any]], stats: PuzzleStats) -> str:
    """The collapsed <summary> line: puzzle pill plus its headline numbers."""
    terminal = next((str(s.get("to", "")).upper() for s in reversed(p_steps)
                     if s.get("type") == "task_end"), "")
    # Pick header style from the actual terminal outcome, not from "any wrong
    # guesses ever" — a solved puzzle that took mistakes en route still reads solved.

    # One-shot puzzles show the scoring breakdown in the header; classic puzzles
    # keep the correct/guesses ratio (which is meaningless for one-shot:
    # groups-matched over a single completion reads as "4/1").
    breakdown = oneshot_score_breakdown(p_steps)
    if breakdown:
        # One-shot inference time comes from the completion's wall_ms —
        # state-move timestamps all land post-response and read as 0s.
        wall_ms = sum(_int0(s.get("wall_ms")) for s in p_steps)
        inline = f"{breakdown} · Cost: ${stats.cost:0.4f}"
        if wall_ms:
            inline += f" · Time: {wall_ms / 1000:.1f}s"
    else:
        pct = f"{stats.correct / stats.guesses * 100:.0f}%" if stats.guesses else "—"
        inline = f"{stats.correct}/{stats.guesses} correct ({pct}) · ${stats.cost:0.4f}"

    return (f"<details class=\"puzzle-block{OUTCOME_CLASS.get(terminal, '')}\">"
            f"<summary class=\"puzzle-summary\">"
            f"<span class=\"pill\">Puzzle {escape_html(pid)}</span>"
            f"<span class=\"puzzle-stats-inline\">{escape_html(inline)}</span>"
            f"</summary>")


def render_run_html(run_id: str, events: List[Event]) -> str:
    # Model/provider come from the first event that names each.
    model = next((m for ev in events if (m := ev.payload.get("model"))), None)
    provider = next((p for ev in events if (p := ev.payload.get("provider"))), None)

    # Group events by puzzle_id, then order by timestamp within each puzzle, so
    # each puzzle's conversation stays a coherent block even for parallel runs.
    # Events without a puzzle_id are dropped.
    puzzle_events: Dict[Any, List[Event]] = {}
    for ev in events:
        pid = ev.payload.get("puzzle_id") if ev.kind in PUZZLE_KINDS else None
        if pid is not None:
            puzzle_events.setdefault(pid, []).append(ev)

    stats_by_puzzle: Dict[str, PuzzleStats] = {}
    eval_by_puzzle: Dict[str, Dict[str, Any]] = {}  # last graded eval, run-scoped
    groups: List[Tuple[Any, List[Dict[str, Any]]]] = []
    for puzzle_id in sorted(puzzle_events, key=_int0):
        p_events = sorted(puzzle_events[puzzle_id], key=lambda e: e.dt)
        p_steps = _puzzle_steps(p_events, str(puzzle_id), stats_by_puzzle, eval_by_puzzle)
        if p_steps:  # a puzzle whose events produced nothing renderable is skipped
            groups.append((puzzle_id, p_steps))

    # Audit order: lowest correct rate first, highest cost first among ties.
    groups.sort(key=lambda g: stats_by_puzzle.get(str(g[0]), NO_STATS).audit_key)

    render_step = {"prompt": lambda s: _bubble("bubble left", "PROMPT", s,
                                               escape_html(s.get("text", ""))),
                   "response": _render_response,
                   "task_end": _render_task_end}

    parts: List[str] = []
    for pid, p_steps in groups:
        parts.append(_puzzle_header(pid, p_steps, stats_by_puzzle.get(str(pid), NO_STATS)))
        parts += [render_step[s["type"]](s) for s in p_steps if s["type"] in render_step]
        parts.append("</details>")

    run = escape_html(run_id)
    return PAGE.substitute(
        title=f"Run {run} · Logs", css=RUN_CSS, heading=f"Run {run}",
        meta=f"{escape_html(provider or '')} · {escape_html(model or '')} · {BACK_LINK}",
        content="".join(parts),
    )


def build_logs_index(pages: List[Tuple[str, Path]]) -> None:
    # Industrial 1980s-themed index. Run ids are "<timestamp>_<model>".
    head = "".join(f"<div class=\"td\" style=\"{TH_STYLE}\">{h}</div>"
                   for h in ("Run ID", "Model", "Action"))
    rows = "".join(
        "<div class=\"tr row\">"
        f"<div class=\"td\">{escape_html(run)}</div>"
        f"<div class=\"td\">{escape_html(run.split('_', 1)[1] if '_' in run else '')}</div>"
        f"<div class=\"td link\"><a href=\"{escape_html(p.name)}\">Open</a></div>"
        "</div>"
        for run, p in sorted(pages)
    )
    page = PAGE.substitute(
        title="Logs", css=INDEX_CSS, heading="Logs", meta=BACK_LINK,
        content=f"<div class=\"thead\"><div class=\"row\">{head}</div></div>{rows}",
    )
    (DOCS_LOG_DIR / "index.html").write_text(page, encoding="utf-8")


def read_allowed_run_ids() -> List[str]:
    """Run ids worth a page: the latest run per (model, mode) among rows with a
    real puzzle count and a recorded cost. Deliberately looser than the
    leaderboard filter in create_results_mviz.py so a run that just misses the
    board still has a transcript to link to."""
    if not RUN_SUMMARIES_CSV.exists():
        return []

    # The one-shot and classic leaderboards each link their own latest run per model.
    best: Dict[str, Tuple[datetime, Optional[str]]] = {}
    with RUN_SUMMARIES_CSV.open("r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                # One-shot runs have exactly one guess per puzzle (<= 20 on the
                # canonical set), so the classic >40 guess floor would exclude
                # every one of them. Only apply it to classic runs.
                mode = (r.get("mode") or "classic").strip() or "classic"
                if (int(r.get("puzzles_attempted", "0") or 0) < 11
                        or (mode != "oneshot"
                            and int(r.get("total_guesses", "0") or 0) <= 40)
                        or r.get("total_cost", "") in ("", None)):
                    continue
            except (TypeError, ValueError):
                continue
            # MotherDuck emits some timestamps tz-naive; normalize to UTC so
            # comparisons don't mix offset-aware and offset-naive datetimes.
            dt = _parse_dt(r.get("start_timestamp", ""))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            key = f"{r.get('model', '')}|{mode}"
            if key not in best or dt > best[key][0]:
                best[key] = (dt, r.get("run_id"))

    return [run_id for _, run_id in best.values() if run_id]


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

    pages: List[Tuple[str, Path]] = [(r, DOCS_LOG_DIR / f"{r}.html")
                                     for r in allowed_run_ids if r not in to_render]

    if to_render:
        runs = group_by_run(load_events_and_postings(to_render))
        for run_id in sorted(to_render):
            evs = runs.get(run_id)
            if not evs:
                print(f"  !! No controllog events for {run_id}; skipping")
                continue
            DOCS_LOG_DIR.mkdir(parents=True, exist_ok=True)
            out = DOCS_LOG_DIR / f"{run_id}.html"
            out.write_text(render_run_html(run_id, evs), encoding="utf-8")
            pages.append((run_id, out))
            print(f"  Wrote {out}")

    build_logs_index(pages)
    print(f"✅ Logs index written to {DOCS_LOG_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
