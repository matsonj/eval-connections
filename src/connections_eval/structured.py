"""Opt-in JSON structured output (`--structured-output`).

Sends OpenRouter a `response_format` JSON schema and renders the returned object
back into the XML-ish text every parser and the linter already understand, so
nothing downstream of `_extract_content` changes. Opt-in because it changes the
response protocol and prompt; results are not comparable with default runs.
"""

import json
import re
from typing import Any, Dict, Optional

_CONFIDENCE = {"type": "number", "minimum": 0, "maximum": 1}
_WORDS4 = {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "string"}}

# Conservative schemas (no enum/anyOf/pattern) so they survive every provider's translation.
_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "oneshot": {
        "type": "object", "additionalProperties": False,
        "required": ["thinking", "answer", "traps", "confidence"],
        "properties": {
            "thinking": {"type": "string"},
            "answer": {"type": "array", "minItems": 4, "maxItems": 4, "items": _WORDS4,
                       "description": "Exactly 4 groups of exactly 4 ALL CAPS puzzle words; all 16 used once."},
            "traps": {"type": "array", "minItems": 0, "maxItems": 4, "items": {"type": "string"},
                      "description": "Exactly 4 ALL CAPS words naming the trap set, or an empty array meaning N/A."},
            "confidence": _CONFIDENCE,
        },
    },
    "classic": {
        "type": "object", "additionalProperties": False,
        "required": ["thinking", "guess", "confidence"],
        "properties": {"thinking": {"type": "string"}, "guess": _WORDS4, "confidence": _CONFIDENCE},
    },
}

# Replaces everything after "RESPONSE FORMAT:" in the XML templates.
_JSON_FORMAT_SECTION: Dict[str, str] = {
    "oneshot": """RESPONSE FORMAT:
Respond with a single JSON object and nothing else. No markdown, no code fence, no prose around it.

The object has exactly these keys:
- "thinking": a string with your reasoning.
- "answer": an array of EXACTLY four arrays, each holding EXACTLY four ALL CAPS words — one inner array per group. Every one of the 16 words is used exactly once.
- "traps": an array of EXACTLY four ALL CAPS words — your single most likely trap set. Use an empty array [] to state N/A; not all puzzles have traps.
- "confidence": a number between 0.0 and 1.0 indicating your confidence in this answer.

Output only the JSON object.
""",
    "classic": """RESPONSE FORMAT:
Respond with a single JSON object and nothing else. No markdown, no code fence, no prose around it.

The object has exactly these keys:
- "thinking": a string with your reasoning.
- "guess": an array of EXACTLY four ALL CAPS words from the puzzle - your one guess.
- "confidence": a number between 0.0 and 1.0 indicating your confidence in this guess.

Output only the JSON object.
""",
}


def build_response_format(mode: str) -> Dict[str, Any]:
    return {"type": "json_schema",
            "json_schema": {"name": f"connections_{mode}", "strict": True, "schema": _SCHEMAS[mode]}}


def json_prompt_template(xml_template: str, mode: str) -> str:
    """Derive the JSON-mode prompt from the XML template by swapping its RESPONSE FORMAT section."""
    return re.sub(r"RESPONSE FORMAT:.*?(?=</user>)", _JSON_FORMAT_SECTION[mode], xml_template, count=1, flags=re.DOTALL)


# --- JSON -> text -------------------------------------------------------------

_INVALID_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')
_ARRAYS = {"answer": re.compile(r'"answer"\s*:\s*(\[\s*\[.*?\]\s*\])', re.DOTALL),
           "guess": re.compile(r'"guess"\s*:\s*(\[[^\[\]]*\])'),
           "traps": re.compile(r'"traps"\s*:\s*(\[[^\[\]]*\])')}
_CONF = re.compile(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)')


def _loads(content: str) -> Optional[Dict[str, Any]]:
    """Parse model JSON tolerantly: code fence, control chars in strings
    (strict=False), invalid escapes, a [{...}] wrapper, and finally salvage the
    answer/guess/traps arrays from text that still won't parse — models break the
    long `thinking` string far more often than the short arrays after it."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rstrip().removesuffix("```").strip()
    if not text:
        return None
    for candidate in (text, _INVALID_ESCAPE.sub(r"\\\\", text)):
        try:
            data = json.loads(candidate, strict=False)
        except (ValueError, TypeError):
            continue
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            data = data[0]
        return data if isinstance(data, dict) else None
    data: Dict[str, Any] = {}
    for key, pat in _ARRAYS.items():
        m = pat.search(text)
        if m:
            try:
                data[key] = json.loads(m.group(1), strict=False)
            except (ValueError, TypeError):
                pass
    m = _CONF.search(text)
    if m:
        data["confidence"] = float(m.group(1))
    return data if ("answer" in data or "guess" in data) else None


def _words(words: Any) -> str:
    return ", ".join(str(w).strip().upper() for w in words if str(w).strip()) if isinstance(words, list) else ""


def render_json_content(content: str, mode: str) -> str:
    """Render a structured-output response as the text protocol. Content that is
    not a usable JSON object is returned unchanged, so a model that ignored the
    schema is scored exactly as in a normal run."""
    data = _loads(content)
    key = "answer" if mode == "oneshot" else "guess"
    if not data or key not in data:
        return content
    parts = []
    if isinstance(data.get("thinking"), str) and data["thinking"].strip():
        parts.append(f"<thinking>{data['thinking'].strip()}</thinking>")
    if mode == "oneshot":
        groups = data["answer"] if isinstance(data["answer"], list) else []
        parts.append("<answer>\n" + "\n".join(filter(None, (_words(g) for g in groups))) + "\n</answer>")
        if "traps" in data:
            parts.append(f"<traps>\n{_words(data['traps']) or 'N/A'}\n</traps>")
    else:
        parts.append(f"<guess>\n{_words(data['guess'])}\n</guess>")
    conf = data.get("confidence")
    if isinstance(conf, (int, float)) and not isinstance(conf, bool):
        parts.append(f"<confidence>\n{conf}\n</confidence>")
    return "\n\n".join(parts)
