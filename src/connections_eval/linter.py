"""Structural linting of model responses.

The eval scores the *puzzle*, not the model's typing. Cheap models routinely
lose every point of a one-shot puzzle because they emit ``ARM WING FIN
FLIPPER`` (spaces) or ``CAR BRANDS: DODGE, FORD, LINCOLN, RAM`` (a label)
instead of the four comma-separated words the RESPONSE FORMAT asks for. Rather
than tokenizing leniently — which would quietly change what the eval measures —
this module validates a response against the prompt's RESPONSE FORMAT and
produces a model-facing message naming the rule that failed, so the model can
re-submit only the broken segment.

Everything here is pure: no I/O, no game state, no knowledge of the solution.
Rules are STRUCTURAL ONLY. A lint message must never leak correctness
information (which groups are right, "one away", how many matched) — the caller
feeds these messages straight back to the model mid-eval.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

__all__ = [
    "LintFailure",
    "LintResult",
    "feedback_message",
    "lint_classic",
    "lint_oneshot",
    "parse_guess_words",
    "splice_segment",
    "strip_thinking",
]


# Reused verbatim from the parsers in core: reasoning blocks are removed
# (including an unclosed one from a truncated response) before any tag is
# looked for, so a worked example inside <thinking> is never linted.
_THINK_CLOSED = re.compile(r'<think(?:ing)?>.*?</think(?:ing)?>', re.IGNORECASE | re.DOTALL)
_THINK_UNCLOSED = re.compile(r'<think(?:ing)?>.*', re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r'<think(?:ing)?>', re.IGNORECASE)
_ANY_TAG = re.compile(r'<[A-Za-z/][^>]*>')

# Same sentinel set _parse_oneshot_traps accepts, so the linter never asks a
# model to re-submit a traps line that would have scored.
_TRAP_SENTINEL = re.compile(r'(?:N/?A|NONE)\.?', re.IGNORECASE)


@dataclass(frozen=True)
class LintFailure:
    """One violated structural rule.

    rule: dotted identifier, e.g. ``answer.words_per_line``. It is echoed to the
        model and embedded in the exchange's result string, so it must stay
        stable and contain no whitespace.
    segment: which block the model must re-submit ("answer", "traps", "guess").
    message: model-facing description of what is wrong, with no trailing period
        (feedback_message supplies the punctuation).
    """
    rule: str
    segment: str
    message: str


@dataclass
class LintResult:
    """Outcome of linting one response."""
    ok: bool
    failures: List[LintFailure] = field(default_factory=list)

    @property
    def first_rule(self) -> Optional[str]:
        return self.failures[0].rule if self.failures else None


# What "re-submit only the failed segment X" concretely means, per segment.
_SEGMENT_INSTRUCTIONS: Dict[str, str] = {
    "answer": ("the complete <answer>...</answer> block (four lines, each with exactly "
               "four words, ALL CAPS, comma-separated) and nothing else"),
    "traps": ("the complete <traps>...</traps> block (ONE line with exactly four words, "
              "ALL CAPS, comma-separated, or N/A) and nothing else"),
    "guess": ("the complete <guess>...</guess> block (exactly four words, ALL CAPS, "
              "comma-separated) and nothing else"),
}


def strip_thinking(content: str) -> str:
    """Remove <think>/<thinking> blocks, including an unclosed trailing one."""
    cleaned = _THINK_CLOSED.sub('', content or '')
    return _THINK_UNCLOSED.sub('', cleaned)


def _thinking_spans(text: str) -> List[Sequence[int]]:
    """Character ranges of reasoning blocks in `text` (closed + one unclosed)."""
    spans = [m.span() for m in _THINK_CLOSED.finditer(text)]
    for m in _THINK_OPEN.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        spans.append((m.start(), len(text)))
        break
    return spans


def _matches_outside_thinking(pattern, text: str) -> List[Any]:
    """Matches of `pattern` in `text` that do not start inside a reasoning block."""
    spans = _thinking_spans(text)
    return [m for m in pattern.finditer(text)
            if not any(s <= m.start() < e for s, e in spans)]


def _segment_pattern(segment: str):
    return re.compile(rf'<{segment}>(.*?)</{segment}>', re.IGNORECASE | re.DOTALL)


def _split_items(line: str) -> List[str]:
    """Split one answer/guess line on commas into non-empty stripped items."""
    return [item.strip() for item in line.split(',') if item.strip()]


def _non_empty_lines(text: str) -> List[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _quote(items: Iterable[str]) -> str:
    return ", ".join(items)


def feedback_message(result: LintResult, segment_hint: Optional[str] = None,
                     protocol: str = "xml") -> str:
    """Render the model-facing text for a failed lint.

    Leads with the first (highest-priority) failure and names the segment to
    re-submit; any remaining failures are appended as a short "Also:" list so
    the model can fix everything in one shot. Returns "" for a passing result.

    protocol "json" is for structured-output runs: the provider forces a whole
    JSON object, so asking for a bare <answer> block would contradict the
    schema (models answer with empty content). The rule text is rewritten in
    JSON terms and the model is asked for the full object with that key fixed.
    """
    if result.ok or not result.failures:
        return ""

    first = result.failures[0]
    segment = segment_hint or first.segment
    if protocol == "json":
        return _feedback_json(result, first, segment)
    instruction = _SEGMENT_INSTRUCTIONS.get(
        segment, f"the complete <{segment}>...</{segment}> block and nothing else")
    text = (f"Response failed linting rule {first.rule}: {first.message.rstrip('.')}. "
            f"Please re-submit only the failed segment {segment}: {instruction}.")

    # Repeats of the lead rule (e.g. every line of a space-separated block
    # failing answer.words_per_line) collapse to a count; the model already has
    # one worked example and the instruction covers the whole block. Other
    # rules are spelled out so everything can be fixed in one re-submission.
    extra = result.failures[1:]
    same_rule = [f for f in extra if f.rule == first.rule]
    other = [f for f in extra if f.rule != first.rule]
    if same_rule:
        n = len(same_rule)
        text += (f" The same rule ({first.rule}) fails on {n} more "
                 f"{'line' if n == 1 else 'lines'} of that block.")
    if other:
        text += " Also: " + "; ".join(
            f"{f.rule}: {f.message.rstrip('.')}" for f in other) + "."
    return text


# JSON-protocol wording for structured-output runs (see feedback_message).
_JSON_SEGMENT_INSTRUCTIONS: Dict[str, str] = {
    "answer": ('the "answer" value corrected: an array of exactly four arrays, each '
               'holding exactly four of the 16 puzzle words in ALL CAPS, every word used '
               'once, no category names or commentary inside the strings'),
    "traps": ('the "traps" value corrected: an array of exactly four puzzle words in ALL '
              'CAPS, or an empty array if there is no trap'),
    "guess": ('the "guess" value corrected: an array of exactly four puzzle words in ALL '
              'CAPS'),
}
_JSON_MISSING = {
    "answer.missing_tag": 'the response was not a valid JSON object with an "answer" array '
                          '(invalid JSON — check for unescaped quotes, backslashes or line '
                          'breaks inside strings — or the key was absent)',
    "guess.missing_tag": 'the response was not a valid JSON object with a "guess" array',
}


def _json_terms(message: str) -> str:
    """Rephrase an XML-protocol rule message in JSON terms."""
    for tag in ("answer", "traps", "guess"):
        message = message.replace(f"<{tag}>...</{tag}> block", f'"{tag}" array')
        message = message.replace(f"<{tag}> block", f'"{tag}" array')
        message = message.replace(f"<{tag}> line", f'"{tag}" array')
    return message.replace("line ", "group ").replace(" lines", " groups")


def _feedback_json(result: LintResult, first: LintFailure, segment: str) -> str:
    message = _JSON_MISSING.get(first.rule) or _json_terms(first.message.rstrip('.'))
    instruction = _JSON_SEGMENT_INSTRUCTIONS.get(
        segment, f'the "{segment}" value corrected')
    text = (f"Response failed linting rule {first.rule}: {message}. "
            f"Please re-submit the complete JSON object required by the response schema "
            f"with {instruction}. Keep \"thinking\" to one short sentence.")
    extra = result.failures[1:]
    same_rule = [f for f in extra if f.rule == first.rule]
    other = [f for f in extra if f.rule != first.rule]
    if same_rule:
        n = len(same_rule)
        text += (f" The same rule ({first.rule}) fails on {n} more "
                 f"{'group' if n == 1 else 'groups'}.")
    if other:
        text += " Also: " + "; ".join(
            f"{f.rule}: {_json_terms(f.message.rstrip('.'))}" for f in other) + "."
    return text


def parse_guess_words(response: str) -> List[str]:
    """Parse a classic-mode response into upper-cased guess words.

    Single source of truth for classic parsing — core._parse_response delegates
    here, so the linter always describes exactly the words the game scored. The
    <guess> block wins; the two fallbacks (an ALL-CAPS comma run, then a plain
    comma split) are kept so tagless-but-parseable guesses still work.
    """
    cleaned = strip_thinking(response)

    guess_match = re.search(r'<guess>(.*?)</guess>', cleaned, re.IGNORECASE | re.DOTALL)
    if guess_match:
        return [w.strip().upper() for w in guess_match.group(1).strip().split(',')
                if w.strip()]

    caps_match = re.search(r'\b[A-Z][A-Z\s]*\b(?:\s*,\s*[A-Z][A-Z\s]*\b){3}', cleaned)
    if caps_match:
        return [w.strip().upper() for w in caps_match.group().split(',') if w.strip()]

    return [w.strip().upper() for w in cleaned.split(',') if w.strip()]


def splice_segment(previous: str, resubmission: str, segment: str) -> str:
    """Merge a re-submitted segment into the model's previous full response.

    The model is asked for one block only, so the rest of its original response
    (its <thinking>, and a <traps> block that already linted clean) has to be
    carried over — otherwise a repair would silently forfeit the trap bonus.

    - resubmission carrying the segment's tags: that block replaces the old one
      (the last one outside <thinking>), or is appended when there was none.
    - resubmission with no tags at all and no other markup: it is wrapped in the
      segment's tags first ("just print the four lines" is the common reply).
    - anything else (empty, or other markup we can't place): previous is kept
      unchanged, so the same lint failure repeats rather than corrupting a
      partly-valid response.
    """
    pattern = _segment_pattern(segment)
    new_clean = strip_thinking(resubmission)

    new_matches = _matches_outside_thinking(pattern, new_clean)
    if new_matches:
        block = new_matches[-1].group(0)
    else:
        body = new_clean.strip()
        if not body or _ANY_TAG.search(body):
            return previous
        block = f"<{segment}>\n{body}\n</{segment}>"

    old_matches = _matches_outside_thinking(pattern, previous)
    if old_matches:
        old = old_matches[-1]
        return previous[:old.start()] + block + previous[old.end():]
    return (previous.rstrip() + "\n\n" + block) if previous.strip() else block


def lint_oneshot(content: str, puzzle_words: List[str]) -> LintResult:
    """Lint a one-shot response against the one-shot RESPONSE FORMAT.

    Rules, in priority order: answer.missing_tag, answer.line_count,
    answer.words_per_line, answer.unknown_word, answer.duplicate_word,
    answer.missing_word, traps.format.

    Word-level rules run only once the block splits into four lines of four
    items each — on a space-separated answer every "word" is unknown and every
    real word missing, and that avalanche buries the one rule that matters.

    A missing <traps> block is not a failure: it forfeits the bonus, exactly as
    it does today. <confidence> is never linted; it does not affect scoring.
    """
    failures: List[LintFailure] = []
    cleaned = strip_thinking(content or "")
    known = {w.strip().upper() for w in puzzle_words}
    available = _quote(sorted(known))

    answer_matches = _matches_outside_thinking(_segment_pattern("answer"), cleaned)
    if not answer_matches:
        failures.append(LintFailure(
            "answer.missing_tag", "answer",
            "the response contains no <answer>...</answer> block"))
        return LintResult(ok=False, failures=failures)

    lines = _non_empty_lines(answer_matches[-1].group(1))
    structure_ok = True

    if len(lines) != 4:
        structure_ok = False
        failures.append(LintFailure(
            "answer.line_count", "answer",
            f"the <answer> block must hold exactly 4 non-empty lines, one per group, "
            f"but it has {len(lines)}"))

    items: List[str] = []
    for index, line in enumerate(lines, start=1):
        line_items = _split_items(line)
        items.extend(item.upper() for item in line_items)
        # "CAR BRANDS: DODGE, FORD, LINCOLN, RAM" splits into four items, so only
        # the label itself gives it away. No puzzle word contains a colon.
        head = line.split(',', 1)[0]
        if ':' in head:
            label = head.split(':', 1)[0].strip()
            structure_ok = False
            failures.append(LintFailure(
                "answer.words_per_line", "answer",
                f'line {index} of the <answer> block, "{line}", is prefixed with the '
                f'category label "{label}:"; every line must be exactly four words, '
                f'ALL CAPS, separated by commas, and nothing else — no category names'))
        elif len(line_items) != 4:
            structure_ok = False
            failures.append(LintFailure(
                "answer.words_per_line", "answer",
                f'line {index} of the <answer> block, "{line}", splits on commas into '
                f'{len(line_items)} item(s); every line must be exactly four words, '
                f'ALL CAPS, separated by commas'))

    if structure_ok:
        unknown = [w for w in items if w not in known]
        if unknown:
            failures.append(LintFailure(
                "answer.unknown_word", "answer",
                f"these submitted words are not puzzle words: {_quote(unknown)}. "
                f"The 16 available words are: {available}"))

        duplicates = sorted({w for w in items if items.count(w) > 1})
        missing = sorted(known - set(items))
        if duplicates:
            failures.append(LintFailure(
                "answer.duplicate_word", "answer",
                f"every puzzle word must be used exactly once, but these are used more "
                f"than once: {_quote(duplicates)}"))
        if missing:
            failures.append(LintFailure(
                "answer.missing_word", "answer",
                f"every puzzle word must be used exactly once, but these are missing: "
                f"{_quote(missing)}"))

    failures.extend(_lint_traps(cleaned, known, available))
    return LintResult(ok=not failures, failures=failures)


def _lint_traps(cleaned: str, known: Set[str], available: str) -> List[LintFailure]:
    """traps.format — only checked when a <traps> block is actually present."""
    matches = _matches_outside_thinking(_segment_pattern("traps"), cleaned)
    if not matches:
        return []

    body = matches[-1].group(1)
    lines = _non_empty_lines(body)
    if lines and _TRAP_SENTINEL.fullmatch(lines[0]):
        return []

    def fail(message: str) -> List[LintFailure]:
        return [LintFailure("traps.format", "traps", message)]

    if not lines:
        return fail("the <traps> block is empty; it must hold ONE line with exactly four "
                    "words, ALL CAPS, comma-separated, or N/A")
    if len(lines) != 1:
        return fail(f"the <traps> block must hold exactly one line (or N/A), but it has "
                    f"{len(lines)}")

    head = lines[0].split(',', 1)[0]
    if ':' in head:
        label = head.split(':', 1)[0].strip()
        return fail(f'the <traps> line, "{lines[0]}", is prefixed with the category label '
                    f'"{label}:"; it must be exactly four words, ALL CAPS, separated by '
                    f'commas, or N/A — no category names')

    items = [item.upper() for item in _split_items(lines[0])]
    if len(items) != 4:
        return fail(f'the <traps> line, "{lines[0]}", splits on commas into {len(items)} '
                    f'item(s); it must be exactly four words, ALL CAPS, separated by '
                    f'commas, or N/A')

    unknown = [w for w in items if w not in known]
    if unknown:
        return fail(f"these trap words are not puzzle words: {_quote(unknown)}. "
                    f"The 16 available words are: {available}")
    return []


def lint_classic(content: str, puzzle_words: List[str],
                 solved_words: Optional[Set[str]] = None) -> LintResult:
    """Lint a classic-mode turn against the classic RESPONSE FORMAT.

    Rules, in priority order: guess.missing_tag, guess.word_count,
    guess.unknown_word, guess.duplicate_word, guess.solved_word.

    A missing <guess> block is reported, but the word rules still run over what
    core's fallback parsing would have extracted — a tagless guess of four valid
    unsolved words is scored today and must keep being scored, so the caller
    (core._process_guess) only lints once _validate_guess has already rejected
    the turn.
    """
    failures: List[LintFailure] = []
    cleaned = strip_thinking(content or "")
    known = {w.strip().upper() for w in puzzle_words}
    solved = {w.strip().upper() for w in (solved_words or set())}
    available = _quote(sorted(known - solved))

    if not _matches_outside_thinking(_segment_pattern("guess"), cleaned):
        failures.append(LintFailure(
            "guess.missing_tag", "guess",
            "the response contains no <guess>...</guess> block"))

    words = parse_guess_words(content or "")
    if len(words) != 4:
        provided = _quote(words) if words else "no valid words"
        failures.append(LintFailure(
            "guess.word_count", "guess",
            f"a guess must be exactly four words, ALL CAPS, separated by commas, but "
            f"{len(words)} were found ({provided}). Available words: {available}"))
        return LintResult(ok=not failures, failures=failures)

    unknown = [w for w in words if w not in known]
    if unknown:
        failures.append(LintFailure(
            "guess.unknown_word", "guess",
            f"these guessed words are not puzzle words: {_quote(unknown)}. "
            f"Available words: {available}"))

    duplicates = sorted({w for w in words if words.count(w) > 1})
    if duplicates:
        failures.append(LintFailure(
            "guess.duplicate_word", "guess",
            f"a guess may not repeat a word, but these are repeated: {_quote(duplicates)}"))

    already_solved = [w for w in words if w in solved]
    if already_solved:
        failures.append(LintFailure(
            "guess.solved_word", "guess",
            f"these words belong to an already solved group and may not be reused: "
            f"{_quote(already_solved)}. Available words: {available}"))

    return LintResult(ok=not failures, failures=failures)
