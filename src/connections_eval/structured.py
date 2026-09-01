"""Opt-in JSON structured output (`--structured-output`).

Cheap models routinely fail the XML-ish response protocol (missing tags,
markdown fences, prose around the answer) and score 0 on format alone. When
structured output is enabled we send OpenRouter a `response_format` JSON schema
so the provider constrains the model to emit a JSON object, then render that
object back into the exact text form the existing parsers, linter and logs
already understand. Nothing downstream of `_extract_content` changes — the
parsers never see JSON.

This is deliberately opt-in: it changes the response protocol (and therefore the
prompt), so results are not directly comparable with XML-format runs.
"""

import json
import re
from typing import Any, Dict, List, Optional

ONESHOT_SCHEMA_NAME = "connections_oneshot"
CLASSIC_SCHEMA_NAME = "connections_classic"

# Keep both schemas conservative — no enums, no anyOf, no pattern — so the same
# payload survives OpenRouter's translation for every provider that supports
# json_schema at all.

_CONFIDENCE_PROPERTY = {
    "type": "number",
    "minimum": 0,
    "maximum": 1,
    "description": "Confidence in this answer, from 0.0 to 1.0.",
}

ONESHOT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["thinking", "answer", "traps", "confidence"],
    "properties": {
        "thinking": {
            "type": "string",
            "description": "Your reasoning.",
        },
        "answer": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "description": (
                "Exactly 4 groups, each exactly 4 ALL CAPS words from the puzzle. "
                "Every one of the 16 words is used exactly once."
            ),
            "items": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
                "items": {"type": "string"},
            },
        },
        "traps": {
            "type": "array",
            "minItems": 0,
            "maxItems": 4,
            "description": (
                "Either exactly 4 ALL CAPS words naming the single most likely trap "
                "set, or an empty array meaning N/A (this puzzle has no trap)."
            ),
            "items": {"type": "string"},
        },
        "confidence": _CONFIDENCE_PROPERTY,
    },
}

CLASSIC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["thinking", "guess", "confidence"],
    "properties": {
        "thinking": {
            "type": "string",
            "description": "Your reasoning.",
        },
        "guess": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "description": "Exactly 4 ALL CAPS words from the puzzle that form one group.",
            "items": {"type": "string"},
        },
        "confidence": _CONFIDENCE_PROPERTY,
    },
}


def build_response_format(mode: str) -> Dict[str, Any]:
    """Build the OpenRouter/OpenAI `response_format` block for an eval mode.

    Args:
        mode: "oneshot" or "classic".

    Returns:
        A `{"type": "json_schema", "json_schema": {...}}` dict ready to drop into
        the chat-completions payload.
    """
    if mode == "oneshot":
        name, schema = ONESHOT_SCHEMA_NAME, ONESHOT_SCHEMA
    else:
        name, schema = CLASSIC_SCHEMA_NAME, CLASSIC_SCHEMA
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


_INVALID_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')
_ANSWER_ARRAY = re.compile(r'"answer"\s*:\s*(\[\s*\[.*?\]\s*\])', re.DOTALL)
_GUESS_ARRAY = re.compile(r'"guess"\s*:\s*(\[[^\[\]]*\])', re.DOTALL)
_TRAPS_ARRAY = re.compile(r'"traps"\s*:\s*(\[[^\[\]]*\])', re.DOTALL)
_CONFIDENCE = re.compile(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)')


def _strip_fence(text: str) -> str:
    if text.startswith("```"):
        # ```json\n{...}\n```  — strip the fence and retry.
        body = text.split("\n", 1)[1] if "\n" in text else ""
        if body.rstrip().endswith("```"):
            body = body.rstrip()[: -len("```")]
        text = body.strip()
    return text


def _salvage(text: str) -> Optional[Dict[str, Any]]:
    """Recover the scoring-relevant arrays from JSON that will not parse.

    Models routinely break the long free-text `thinking` string (raw newlines,
    stray backslashes) while the short `answer`/`guess`/`traps` arrays at the
    end are well formed. Those arrays hold only plain strings, so they can be
    parsed on their own. `thinking` is dropped; the linter never reads it.
    """
    data: Dict[str, Any] = {}
    for key, pattern in (("answer", _ANSWER_ARRAY), ("guess", _GUESS_ARRAY),
                         ("traps", _TRAPS_ARRAY)):
        m = pattern.search(text)
        if not m:
            continue
        try:
            data[key] = json.loads(m.group(1), strict=False)
        except (ValueError, TypeError):
            continue
    m = _CONFIDENCE.search(text)
    if m:
        try:
            data["confidence"] = float(m.group(1))
        except ValueError:
            pass
    return data if ("answer" in data or "guess" in data) else None


def _loads(content: str) -> Optional[Dict[str, Any]]:
    """Parse `content` as a JSON object, tolerating the ways models break JSON.

    In order: a markdown code fence; literal control characters inside strings
    (`strict=False`); invalid backslash escapes (re-escaped); and finally a
    salvage pass that extracts just the answer/guess/traps arrays from text that
    still will not parse. Returns None when nothing usable is found — callers
    then leave the text untouched so the existing parsers handle it exactly as
    they do today.
    """
    text = _strip_fence((content or "").strip())
    if not text:
        return None
    for candidate in (text, _INVALID_ESCAPE.sub(r"\\\\", text)):
        try:
            data = json.loads(candidate, strict=False)
        except (ValueError, TypeError):
            continue
        # Some models wrap the object in a one-element array: [{...}].
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        return data if isinstance(data, dict) else None
    return _salvage(text)


def _render_words(words: Any) -> str:
    """Render one group as `W1, W2, W3, W4` — upper-cased, whitespace trimmed."""
    if isinstance(words, str):
        return words.strip().upper()
    if not isinstance(words, (list, tuple)):
        return ""
    rendered = [str(w).strip().upper() for w in words]
    return ", ".join(w for w in rendered if w)


def _render_confidence(value: Any) -> str:
    if value is None or isinstance(value, bool):
        # bool is an int subclass but is never a confidence; None means the key
        # was absent, and an absent confidence renders no block at all.
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip()


def _block(tag: str, body: str) -> str:
    return f"<{tag}>\n{body}\n</{tag}>"


def _render_thinking(data: Dict[str, Any]) -> List[str]:
    thinking = data.get("thinking")
    if not isinstance(thinking, str) or not thinking.strip():
        return []
    return [f"<thinking>{thinking.strip()}</thinking>"]


def _render_tail(data: Dict[str, Any]) -> List[str]:
    confidence = _render_confidence(data.get("confidence"))
    return [_block("confidence", confidence)] if confidence else []


def render_oneshot(data: Dict[str, Any]) -> str:
    """Render a one-shot JSON object into the XML-ish text the parsers expect."""
    answer = data["answer"]
    lines = [_render_words(group) for group in answer] if isinstance(answer, list) else []
    parts = _render_thinking(data)
    parts.append(_block("answer", "\n".join(line for line in lines if line)))

    if "traps" in data:
        traps = data["traps"]
        body = _render_words(traps)
        # An empty array is the model's explicit "no trap here" claim, which the
        # scorer reads from the N/A sentinel.
        parts.append(_block("traps", body or "N/A"))

    parts.extend(_render_tail(data))
    return "\n\n".join(parts)


def render_classic(data: Dict[str, Any]) -> str:
    """Render a classic JSON object into the XML-ish text the parsers expect."""
    parts = _render_thinking(data)
    parts.append(_block("guess", _render_words(data["guess"])))
    parts.extend(_render_tail(data))
    return "\n\n".join(parts)


def render_json_content(content: str, mode: str) -> str:
    """Normalize a structured-output response to the legacy text protocol.

    Returns `content` unchanged when it is not a JSON object carrying the key
    this mode needs, so a model that ignored the schema is scored exactly as it
    would be in a normal run.
    """
    data = _loads(content)
    if data is None:
        return content
    key = "answer" if mode == "oneshot" else "guess"
    if key not in data:
        return content
    return render_oneshot(data) if mode == "oneshot" else render_classic(data)
