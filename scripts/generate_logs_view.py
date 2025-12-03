#!/usr/bin/env python3
"""
Generate chat-style HTML logs per run from MotherDuck controllog tables.

Outputs to docs/logs/<run_id>.html and an optional docs/logs/index.html.

Requirements/assumptions (v2.0.1+ controllog):
- controllog.events table contains kinds: model_prompt, model_completion, model_response, state_move
- Each event includes run_id and (for puzzle steps) puzzle_id, guess_index, result, request_text, response_text
- controllog.postings table contains resource.tokens and resource.money postings keyed by event_id

This script:
- Queries controllog.events and controllog.postings from MotherDuck (or local DuckDB)
- Groups events by run_id, then by puzzle_id, ordered by event_time
- Renders a chat-like transcript with: prompt/thinking (left), response/guess (right), game state line, token/cost sidebar

Environment:
- MOTHERDUCK_DB: MotherDuck database connection string (default: "md:")
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from pathlib import Path
import re
import duckdb  # type: ignore


DOCS_LOG_DIR = Path("docs/logs")
RUN_SUMMARIES_CSV = Path("results/run_summaries.csv")


@dataclass
class Posting:
    event_id: str
    account_type: str
    unit: str
    delta: float
    dims: Dict[str, Any]


@dataclass
class Event:
    event_id: str
    event_time: str
    kind: str
    run_id: str
    payload: Dict[str, Any]
    raw: Dict[str, Any]
    postings: List[Posting] = field(default_factory=list)

    @property
    def dt(self) -> datetime:
        try:
            return datetime.fromisoformat(self.raw.get("event_time").replace("Z", "+00:00"))
        except Exception:
            return datetime.min


def struct_to_dict(struct_value: Any) -> Dict[str, Any]:
    """Convert a DuckDB STRUCT to a Python dict.
    
    DuckDB STRUCTs are already returned as dicts when using .df(),
    but we handle None and ensure it's a dict.
    """
    if struct_value is None:
        return {}
    if isinstance(struct_value, dict):
        return struct_value
    # Fallback: try to convert if it's not already a dict
    try:
        return dict(struct_value) if hasattr(struct_value, '__iter__') and not isinstance(struct_value, str) else {}
    except Exception:
        return {}


def load_events_and_postings(db: str = "md:") -> Dict[str, Event]:
    """Load events and postings from MotherDuck controllog tables."""
    event_by_id: Dict[str, Event] = {}
    
    print(f"Connecting to MotherDuck database: {db}")
    con = duckdb.connect(db)
    
    try:
        # Query all events
        print("  Loading events...")
        events_query = """
        SELECT 
            event_id,
            event_time,
            ingest_time,
            kind,
            actor_agent_id,
            actor_task_id,
            project_id,
            run_id,
            source,
            idempotency_key,
            payload_json
        FROM controllog.events
        ORDER BY event_time
        """
        events_df = con.execute(events_query).df()
        
        # Convert events to Event objects
        for _, row in events_df.iterrows():
            # Convert payload_json STRUCT to dict
            payload = struct_to_dict(row.get("payload_json"))
            
            # Build raw record (all fields)
            raw_rec = {
                "event_id": str(row.get("event_id", "")),
                "event_time": str(row.get("event_time", "")),
                "ingest_time": str(row.get("ingest_time", "")),
                "kind": str(row.get("kind", "")),
                "actor_agent_id": str(row.get("actor_agent_id", "")) if row.get("actor_agent_id") else None,
                "actor_task_id": str(row.get("actor_task_id", "")) if row.get("actor_task_id") else None,
                "project_id": str(row.get("project_id", "")),
                "run_id": str(row.get("run_id", "")) if row.get("run_id") else None,
                "source": str(row.get("source", "")),
                "idempotency_key": str(row.get("idempotency_key", "")),
                "payload_json": payload,
            }
            
            ev = Event(
                event_id=raw_rec.get("event_id"),
                event_time=raw_rec.get("event_time"),
                kind=raw_rec.get("kind"),
                run_id=raw_rec.get("run_id"),
                payload=payload,
                raw=raw_rec,
            )
            if ev.event_id:
                event_by_id[ev.event_id] = ev
        
        print(f"  Loaded {len(event_by_id)} events")
        
        # Query all postings
        print("  Loading postings...")
        postings_query = """
        SELECT 
            posting_id,
            event_id,
            account_type,
            account_id,
            unit,
            delta_numeric,
            dims_json
        FROM controllog.postings
        """
        postings_df = con.execute(postings_query).df()
        
        # Attach postings to events
        posting_count = 0
        for _, row in postings_df.iterrows():
            eid = str(row.get("event_id", ""))
            if not eid:
                continue
            ev = event_by_id.get(eid)
            if not ev:
                # Postings might reference events that don't exist; skip if missing
                continue
            
            # Convert dims_json STRUCT to dict
            dims = struct_to_dict(row.get("dims_json"))
            
            ev.postings.append(
                Posting(
                    event_id=eid,
                    account_type=str(row.get("account_type", "")),
                    unit=str(row.get("unit", "")),
                    delta=float(row.get("delta_numeric", 0)),
                    dims=dims,
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
    for run_id, evs in runs.items():
        evs.sort(key=lambda e: e.dt)
    return runs


def summarize_tokens_and_cost(postings: List[Posting]) -> Dict[str, Any]:
    prompt_tokens = 0
    completion_tokens = 0
    money = 0.0
    for p in postings:
        if p.account_type == "resource.tokens" and p.unit == "+tokens":
            # project:+tokens is +N when consuming N tokens; provider:-N mirrors
            # When dims has phase, we can split by prompt/completion
            phase = str(p.dims.get("phase", "")).lower()
            if phase == "prompt":
                prompt_tokens += int(abs(p.delta))
            elif phase == "completion":
                completion_tokens += int(abs(p.delta))
        if p.account_type == "resource.money" and p.unit == "$":
            money += abs(float(p.delta))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost": money if money > 0 else None,
    }


def escape_html(text: Optional[str]) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def extract_state_transition(ev: Event) -> Optional[Tuple[str, str, str, Optional[str]]]:
    """Return (task_id, from, to, puzzle_id_str) when a task changes state.

    puzzle_id_str is best-effort parsed from task id or payload.
    """
    pid_str: Optional[str] = None
    # Prefer payload puzzle id if present (e.g., state_move)
    p = ev.payload or {}
    if "puzzle_id" in p:
        pid_str = str(p.get("puzzle_id"))
    acc = ev.raw.get("actor_task_id") or ev.raw.get("actor", {}).get("task_id") or ""
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
            try:
                after_t = acc.split(":", 1)[0]
                if after_t.startswith("T"):
                    pid_str = after_t[1:]
            except Exception:
                pid_str = None
        return (acc, from_val or "WIP", to_val or "", pid_str)
    return None


def split_thinking_blocks(text: str) -> Tuple[str, str]:
    """Split response_text into (thinking, rest) by <thinking> tags."""
    if not text:
        return "", ""
    lower = text
    start = lower.find("<thinking>")
    end = lower.find("</thinking>")
    if start != -1 and end != -1 and end > start:
        thinking = text[start + len("<thinking>"):end].strip()
        rest = (text[:start] + text[end + len("</thinking>"):]).strip()
        return thinking, rest
    return "", text.strip()


_GUESS_RE = re.compile(r"<guess>\s*([A-Z ,\-]+)\s*</guess>")


def extract_guess_words(text: str) -> Optional[List[str]]:
    """Best-effort parse of guessed words from response text.

    Looks for <guess> BLOCK </guess>, returns list of up to 4 tokens.
    """
    if not text:
        return None
    m = _GUESS_RE.search(text)
    if not m:
        return None
    payload = m.group(1)
    words = [w.strip() for w in payload.split(',') if w.strip()]
    return words[:4] if words else None


def render_run_html(run_id: str, events: List[Event]) -> str:
    # Basic CSS for chat layout
    css = (
        "body{font-family:ui-monospace,Menlo,Consolas,Monaco,\"Courier New\",monospace;margin:0;padding:0;"
        "background:repeating-linear-gradient(0deg,#e9ecef,#e9ecef 24px,#eff2f5 25px);color:#0f1419;}"
        ".container{max-width:1024px;margin:0 auto;padding:24px;}"
        ".panel{background:#ffffff;border:2px solid #2b3035;box-shadow:inset 0 0 0 1px #d9dde1;}") + (
        ".topbar{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;"
        "background:#1f2a36;color:#e6eef7;border-bottom:2px solid #2b3035;letter-spacing:0.04em;text-transform:uppercase;}"
        ".title{font-weight:700;} .meta{color:#8aa0b8;font-size:12px;}"
        ".content{padding:16px;}"
        ".row{display:flex;flex-direction:column;margin:8px 0;}"
        ".bubble{max-width:76%;padding:0;border:2px solid #2b3035;background:#fff;}") + (
        ".bubble .metahead{background:#f2f5f8;border-bottom:2px solid #2b3035;padding:6px 10px;"
        "font-size:11px;text-transform:uppercase;letter-spacing:0.04em;color:#28323b;display:flex;justify-content:space-between;}"
        ".bubble .body{padding:12px 14px;white-space:pre-wrap;word-wrap:break-word;}"
        ".left{align-self:flex-start;border-left:6px solid #6c757d;}"
        ".right{align-self:flex-end;border-left:6px solid #0d6efd;background:#f7fbff;}"
        ".step{border-left:4px solid #2b3035;padding-left:12px;margin:18px 0;}"
        ".state{font-size:12px;color:#34424f;margin-left:2px;margin-top:6px;}"
        ".stats{font-size:12px;color:#1f2a36;margin-top:6px;}"
        ".pill{display:inline-block;background:#f0f3f6;border:2px solid #2b3035;border-radius:0;padding:2px 8px;margin-right:6px;color:#1f2a36;}"
        ".endpill{display:inline-block;background:#e7f5ec;border:2px solid #2b3035;color:#1f3b2a;border-radius:0;padding:4px 10px;margin:10px 0;}") + (
        "details{margin:8px 0;} details>summary{cursor:pointer;color:#0b63ce;list-style: disclosure-closed;padding-right:16px;margin-right:4px;}"
        "details[open]>summary{list-style: disclosure-open;}"
        ".thinking{font-style:italic;font-size:12px;color:#2b333b;white-space:pre-wrap;}"
        ".footer{margin:24px;color:#48525c;font-size:12px;}"
        "a{color:#0b63ce;text-decoration:none;}a:hover{text-decoration:underline;}"
    )

    # Determine model/provider from first event with payload
    model = None
    provider = None
    for ev in events:
        model = model or ev.payload.get("model")
        provider = provider or ev.payload.get("provider")
        if model and provider:
            break

    # Build steps grouped by puzzle_id and guess_index order
    # We collect all events and also mark game end transitions
    steps: List[Dict[str, Any]] = []
    current_puzzle = None
    state_increments = 0
    # Track last evaluation per puzzle (to show why a puzzle ended)
    last_eval_by_puzzle: Dict[str, Dict[str, Any]] = {}
    # Accumulate per-puzzle stats
    puzzle_stats: Dict[str, Dict[str, Any]] = {}

    for ev in events:
        kind = ev.kind
        p = ev.payload
        if kind in ("model_prompt", "model_completion", "model_response"):
            puzzle_id = p.get("puzzle_id")
            guess_index = p.get("guess_index")
            if current_puzzle != puzzle_id and puzzle_id is not None:
                current_puzzle = puzzle_id
                steps.append({"type": "puzzle_header", "puzzle_id": puzzle_id})

            if kind == "model_prompt":
                steps.append({
                    "type": "prompt",
                    "text": p.get("request_text", ""),
                    "tokens": summarize_tokens_and_cost(ev.postings),
                    "ts": ev.event_time,
                })
                if puzzle_id is not None:
                    pid = str(puzzle_id)
                    ps = puzzle_stats.setdefault(pid, {"prompt": 0, "completion": 0, "cost": 0.0, "guesses": 0, "correct": 0, "start_dt": None})
                    tok = summarize_tokens_and_cost(ev.postings)
                    ps["prompt"] += int(tok.get("prompt_tokens") or 0)
                    ps["completion"] += int(tok.get("completion_tokens") or 0)
                    ps["cost"] += float(tok.get("cost") or 0.0)
                    if ps["start_dt"] is None:
                        ps["start_dt"] = ev.dt
            elif kind == "model_completion":
                # Completion is often the response chunk for some providers; capture text
                steps.append({
                    "type": "response",
                    "text": p.get("response_text", ""),
                    "puzzle_id": puzzle_id,
                    "tokens": summarize_tokens_and_cost(ev.postings),
                    "ts": ev.event_time,
                })
                if puzzle_id is not None:
                    pid = str(puzzle_id)
                    ps = puzzle_stats.setdefault(pid, {"prompt": 0, "completion": 0, "cost": 0.0, "guesses": 0, "correct": 0, "start_dt": None})
                    tok = summarize_tokens_and_cost(ev.postings)
                    ps["prompt"] += int(tok.get("prompt_tokens") or 0)
                    ps["completion"] += int(tok.get("completion_tokens") or 0)
                    ps["cost"] += float(tok.get("cost") or 0.0)
                    # Try to infer a guess from response_text
                    gw = extract_guess_words(p.get("response_text", ""))
                    if gw:
                        ps["guesses"] += 1
                    if ps["start_dt"] is None:
                        ps["start_dt"] = ev.dt
            elif kind == "model_response":
                # Consolidated response with result/guess
                # Update last eval by puzzle
                eval_info = {
                    "guess_index": guess_index,
                    "result": p.get("result"),
                    "response_text": p.get("response_text"),
                    "ts": ev.event_time,
                }
                if puzzle_id is not None:
                    last_eval_by_puzzle[str(puzzle_id)] = eval_info
                    pid = str(puzzle_id)
                    ps = puzzle_stats.setdefault(pid, {"prompt": 0, "completion": 0, "cost": 0.0, "guesses": 0, "correct": 0, "start_dt": None})
                    tok = summarize_tokens_and_cost(ev.postings)
                    ps["prompt"] += int(tok.get("prompt_tokens") or 0)
                    ps["completion"] += int(tok.get("completion_tokens") or 0)
                    ps["cost"] += float(tok.get("cost") or 0.0)
                    # Count this as a guess attempt
                    ps["guesses"] += 1
                    res = str(p.get("result", ""))
                    if "CORRECT" in res.upper():
                        ps["correct"] += 1
                    if ps["start_dt"] is None:
                        ps["start_dt"] = ev.dt
                steps.append({
                    "type": "response",
                    "text": p.get("response_text") or p.get("result", ""),
                    "result": p.get("result"),
                    "guess_index": guess_index,
                    "puzzle_id": puzzle_id,
                    "tokens": summarize_tokens_and_cost(ev.postings),
                    "ts": ev.event_time,
                })
        if kind == "state_move":
            # Mark state transitions between puzzles
            state_increments += 1
            steps.append({"type": "state", "text": f"State advanced ({state_increments})", "ts": ev.event_time})

        # Check for end transitions
        st = extract_state_transition(ev)
        if st:
            task_id, frm, to, pid_str = st
            # Derive puzzle id
            puzzle_label = pid_str or task_id.split(":")[0].lstrip("task:")
            reason = "SOLVED" if str(to).upper() == "DONE" else ("FAILED" if str(to).upper() == "ERROR" else str(to).upper())
            final_eval = last_eval_by_puzzle.get(str(puzzle_label)) or {}
            # Prepare summary for this puzzle
            summary: Dict[str, Any] = {}
            ps = puzzle_stats.get(str(puzzle_label))
            if ps:
                prompt_total = int(ps.get("prompt", 0))
                completion_total = int(ps.get("completion", 0))
                guesses_total = int(ps.get("guesses", 0))
                correct_total = int(ps.get("correct", 0))
                cost_total = float(ps.get("cost", 0.0))
                # time delta
                start_dt = ps.get("start_dt")
                end_dt = ev.dt
                if start_dt and end_dt:
                    seconds = int((end_dt - start_dt).total_seconds())
                    mm = seconds // 60
                    ss = seconds % 60
                    time_str = f"{mm:02d}:{ss:02d}"
                else:
                    time_str = "--:--"
                summary = {
                    "prompt_tokens": prompt_total,
                    "completion_tokens": completion_total,
                    "guesses": guesses_total,
                    "correct": correct_total,
                    "time": time_str,
                    "cost": cost_total,
                }
            steps.append({
                "type": "task_end",
                "text": f"{puzzle_label} → {reason}",
                "from": frm,
                "to": to,
                "final_eval": final_eval,
                "summary": summary,
                "ts": ev.event_time,
            })

    # Render HTML
    parts: List[str] = []
    parts.append("<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">")
    parts.append(f"<title>Run {escape_html(run_id)} · Logs</title>")
    parts.append(f"<style>{css}</style></head><body>")
    parts.append("<div class=\"container\">")
    parts.append("<div class=\"panel\">")
    parts.append("<div class=\"topbar\">")
    parts.append(f"<div class=\"title\">Run {escape_html(run_id)}</div>")
    parts.append(f"<div class=\"meta\">{escape_html(provider or '')} · {escape_html(model or '')} · <a href=\"../index.html\" style=\"color:#b6c7da\">Back</a></div>")
    parts.append("</div>")
    parts.append("<div class=\"content\">")

    for step in steps:
        st = step.get("type")
        if st == "puzzle_header":
            parts.append(f"<div class=\"step\"><div class=\"pill\">Puzzle {escape_html(step.get('puzzle_id'))}</div></div>")
            continue
        if st == "state":
            parts.append(f"<div class=\"state\">{escape_html(step.get('text'))}</div>")
            continue
        if st == "task_end":
            parts.append(f"<div class=\"endpill\">{escape_html(step.get('text'))} <span class=\"meta\">({escape_html(step.get('from',''))} → {escape_html(step.get('to',''))})</span></div>")
            fe = step.get("final_eval", {})
            if fe:
                gi = fe.get("guess_index")
                res = fe.get("result")
                rtxt = fe.get("response_text")
                details_bits = []
                if res:
                    details_bits.append(f"Result: {escape_html(str(res))}")
                if gi is not None:
                    details_bits.append(f"Guess #: {escape_html(str(gi))}")
                if rtxt:
                    # Split and show thinking collapsed if present
                    th, rr = split_thinking_blocks(rtxt)
                    inner = []
                    if th:
                        inner.append(f"<details><summary>Show final thinking</summary><div class=\"thinking\">{escape_html(th)}</div></details>")
                    if rr:
                        inner.append(f"<div>{escape_html(rr)}</div>")
                    else:
                        inner.append(f"<div>{escape_html(rtxt)}</div>")
                    details_bits.append(" ".join(inner))
                parts.append(f"<div class=\"stats\">{' · '.join(details_bits)}</div>")
            # Summary ledger (both sides)
            summ = step.get("summary", {})
            if summ:
                pt = summ.get("prompt_tokens", 0)
                ct = summ.get("completion_tokens", 0)
                gs = summ.get("guesses", 0)
                cg = summ.get("correct", 0)
                tm = summ.get("time", "--:--")
                cost = summ.get("cost", 0.0)
                parts.append(
                    "<div class=\"stats\">"
                    f"prompt: {pt:,} · completion: {ct:,} · guesses: {gs} · correct: {cg} · time: {tm} · cost: ${cost:0.4f}"
                    "</div>"
                )
            continue
        if st == "prompt":
            t = escape_html(step.get("text", ""))
            tok = step.get("tokens", {})
            parts.append("<div class=\"row\">")
            parts.append("<div class=\"bubble left\">")
            parts.append(f"<div class=\"metahead\"><span>PROMPT</span><span>{escape_html(step.get('ts',''))}</span></div>")
            parts.append(f"<div class=\"body\">{t}</div>")
            parts.append("</div>")
            stats = []
            if tok.get("prompt_tokens"):
                stats.append(f"prompt: {tok['prompt_tokens']:,}")
            if tok.get("completion_tokens"):
                stats.append(f"completion: {tok['completion_tokens']:,}")
            if tok.get("cost"):
                stats.append(f"cost: ${tok['cost']:.6f}")
            if stats:
                parts.append(f"<div class=\"stats\">{' · '.join(stats)}</div>")
            parts.append("</div>")
            continue
        if st == "response":
            raw_text = step.get("text", "")
            thinking, rest = split_thinking_blocks(raw_text)
            tok = step.get("tokens", {})
            parts.append("<div class=\"row\">")
            bubble_inner: List[str] = []
            if thinking:
                bubble_inner.append(
                    f"<details><summary>Show thinking</summary><div class=\"thinking\">{escape_html(thinking)}</div></details>"
                )
            if rest:
                bubble_inner.append(f"<div>{escape_html(rest)}</div>")
            else:
                bubble_inner.append(f"<div>{escape_html(raw_text)}</div>")
            parts.append("<div class=\"bubble right\">")
            # Meta header: include guess index and result if present
            gh = []
            if step.get("guess_index") is not None:
                gh.append(f"Guess {escape_html(str(step['guess_index']))}")
            if step.get("result"):
                gh.append(escape_html(str(step.get("result"))))
            meta_right = " · ".join(gh) if gh else "RESPONSE"
            parts.append(f"<div class=\"metahead\"><span>{meta_right}</span><span>{escape_html(step.get('ts',''))}</span></div>")
            parts.append(f"<div class=\"body\">{''.join(bubble_inner)}</div>")
            parts.append("</div>")
            stats = []
            if tok.get("prompt_tokens"):
                stats.append(f"prompt: {tok['prompt_tokens']:,}")
            if tok.get("completion_tokens"):
                stats.append(f"completion: {tok['completion_tokens']:,}")
            if tok.get("cost"):
                stats.append(f"cost: ${tok['cost']:.6f}")
            if step.get("result"):
                stats.append(f"result: {escape_html(step['result'])}")
            if stats:
                parts.append(f"<div class=\"stats\">{' · '.join(stats)}</div>")
            parts.append("</div>")
            continue

    parts.append("<div class=\"footer\">Generated by generate_logs_view.py</div>")
    parts.append("</div></div></div></body></html>")
    return "".join(parts)


def write_run_page(run_id: str, html: str) -> Path:
    DOCS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = DOCS_LOG_DIR / f"{run_id}.html"
    out.write_text(html, encoding="utf-8")
    return out


def build_logs_index(pages: List[Tuple[str, Path]]) -> None:
    # Industrial 1980s-themed index
    css = (
        "body{font-family:ui-monospace,Menlo,Consolas,Monaco,\\\"Courier New\\\",monospace;margin:0;padding:0;"
        "background:repeating-linear-gradient(0deg,#e9ecef,#e9ecef 24px,#eff2f5 25px);color:#0f1419;}"
        ".container{max-width:1024px;margin:0 auto;padding:24px;}"
        ".panel{background:#ffffff;border:2px solid #2b3035;box-shadow:inset 0 0 0 1px #d9dde1;}"
        ".topbar{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:#1f2a36;color:#e6eef7;"
        "border-bottom:2px solid #2b3035;letter-spacing:0.04em;text-transform:uppercase;}"
        ".title{font-weight:700;} .meta{color:#8aa0b8;font-size:12px;}"
        ".content{padding:16px;}"
        ".table{width:100%;border-collapse:separate;border-spacing:0;}"
        ".thead th{background:#f2f5f8;border:2px solid #2b3035;border-bottom:none;padding:8px 10px;text-align:left;font-size:12px;letter-spacing:.04em;text-transform:uppercase;}"
        ".row{display:grid;grid-template-columns: 48% 32% 20%;align-items:center;}"
        ".tr{border:2px solid #2b3035;border-top:none;background:#fff;}"
        ".td{padding:10px 12px;border-right:2px solid #2b3035;} .td:last-child{border-right:none;}"
        ".link a{color:#0b63ce;text-decoration:none;} .link a:hover{text-decoration:underline;}"
        ".footer{margin:16px 0 0 0;color:#48525c;font-size:12px;}"
    )

    # Prepare rows (derive timestamp/model from run_id if possible)
    def split_run(run_id: str) -> Tuple[str, str]:
        if "_" in run_id:
            ts, model = run_id.split("_", 1)
        else:
            ts, model = run_id, ""
        return ts, model

    rows_html = []
    for run, p in sorted(pages):
        ts, model = split_run(run)
        rows_html.append(
            "".join(
                [
                    "<div class=\"tr row\">",
                    f"<div class=\"td\">{escape_html(run)}</div>",
                    f"<div class=\"td\">{escape_html(model)}</div>",
                    f"<div class=\"td link\"><a href=\"{p.name}\">Open</a></div>",
                    "</div>",
                ]
            )
        )

    html = (
        "<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Logs</title>" f"<style>{css}</style></head><body>"
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

    (DOCS_LOG_DIR / "index.html").write_text(html, encoding="utf-8")


def main():
    print("🧭 Loading controllog events and postings…")
    
    # Get MotherDuck database connection string from environment
    db = os.environ.get("MOTHERDUCK_DB", "md:")
    
    events_by_id = load_events_and_postings(db)
    print(f"  Loaded {len(events_by_id)} events")

    print("📚 Grouping by run…")
    runs = group_by_run(events_by_id)
    print(f"  Found {len(runs)} runs")

    # Restrict to the same dataset as the results table, version >= 2.0.1, latest per model
    allowed_run_ids: List[str] = []
    if RUN_SUMMARIES_CSV.exists():
        import csv
        from datetime import timezone
        rows: List[Dict[str, Any]] = []
        with RUN_SUMMARIES_CSV.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                try:
                    # Filters matching create_results_table_gt.py
                    if int(r.get("puzzles_attempted", "0") or 0) < 11:
                        continue
                    if int(r.get("total_guesses", "0") or 0) <= 40:
                        continue
                    if r.get("total_cost", "") in ("", None):
                        continue
                    # Version filter >= 2.0.1
                    def vtuple(v: str):
                        parts = [int(x) for x in str(v).split('.')[:3]]
                        while len(parts) < 3:
                            parts.append(0)
                        return tuple(parts)
                    if vtuple(r.get("version", "0.0.0")) < (2, 0, 1):
                        continue
                    # Keep row
                    rows.append(r)
                except Exception:
                    continue
        # Select latest per (model, version) by start_timestamp
        best_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for r in rows:
            model = r.get("model", "")
            ver = r.get("version", "")
            ts = r.get("start_timestamp", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                dt = datetime.min
            key = (model, ver)
            existing = best_by_key.get(key)
            if existing is None or dt > existing["dt"]:
                best_by_key[key] = {"row": r, "dt": dt}
        allowed_run_ids = [val["row"].get("run_id") for val in best_by_key.values() if val["row"].get("run_id")]
        print(f"  Filtered to {len(allowed_run_ids)} latest runs per model+version (v>=2.0.1)")

    # Generate only for allowed runs; if none, do not emit per-run pages
    if allowed_run_ids:
        target_runs = {rid: evs for rid, evs in runs.items() if rid in set(allowed_run_ids)}
    else:
        target_runs = {}

    pages: List[Tuple[str, Path]] = []
    for run_id, evs in sorted(target_runs.items()):
        html = render_run_html(run_id, evs)
        out = write_run_page(run_id, html)
        pages.append((run_id, out))
        print(f"  Wrote {out}")

    build_logs_index(pages)
    print(f"✅ Logs index written to {DOCS_LOG_DIR / 'index.html'}")


if __name__ == "__main__":
    main()


