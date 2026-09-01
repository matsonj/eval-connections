"""Structural linting of one-shot responses.

Cheap models lose whole puzzles to format slips (``ARM WING FIN FLIPPER`` with
no commas, ``CAR BRANDS: DODGE, FORD, LINCOLN, RAM`` with a label). Instead of
tokenizing leniently, the eval tells the model which rule it broke and asks for
just that segment again. Rules are structural only and never leak correctness.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_THINK_CLOSED = re.compile(r'<think(?:ing)?>.*?</think(?:ing)?>', re.IGNORECASE | re.DOTALL)
_THINK_UNCLOSED = re.compile(r'<think(?:ing)?>.*', re.IGNORECASE | re.DOTALL)
_ANY_TAG = re.compile(r'<[A-Za-z/][^>]*>')
_TRAP_SENTINEL = re.compile(r'(?:N/?A|NONE)\.?', re.IGNORECASE)  # same set the scorer accepts


@dataclass(frozen=True)
class LintFailure:
    rule: str      # dotted id, e.g. answer.words_per_line; embedded in the result string
    segment: str   # block to re-submit: "answer" or "traps"
    message: str


@dataclass
class LintResult:
    ok: bool
    failures: List[LintFailure] = field(default_factory=list)

    @property
    def first_rule(self) -> Optional[str]:
        return self.failures[0].rule if self.failures else None


def strip_thinking(content: str) -> str:
    """Remove <think>/<thinking> blocks, including an unclosed trailing one."""
    return _THINK_UNCLOSED.sub('', _THINK_CLOSED.sub('', content or ''))


def _block(text: str, segment: str):
    """Last <segment>...</segment> match outside thinking, or None."""
    matches = list(re.finditer(rf'<{segment}>(.*?)</{segment}>', strip_thinking(text),
                               re.IGNORECASE | re.DOTALL))
    return matches[-1] if matches else None


def _items(line: str) -> List[str]:
    return [i.strip().upper() for i in line.split(',') if i.strip()]


def _label(line: str) -> Optional[str]:
    """A category label before the first comma ("CAR BRANDS: DODGE, ..."). No puzzle word has a colon."""
    head = line.split(',', 1)[0]
    return head.split(':', 1)[0].strip() if ':' in head else None


def lint_oneshot(content: str, puzzle_words: List[str]) -> LintResult:
    """Rules in priority order: answer.missing_tag, answer.line_count,
    answer.words_per_line, answer.unknown_word, answer.duplicate_word,
    answer.missing_word, traps.format. Word rules run only once the block is
    4 lines of 4 items, so a space-separated answer reports one rule, not 32.
    A missing <traps> block is not a failure (it forfeits the bonus, as today)."""
    known = {w.strip().upper() for w in puzzle_words}
    available = ", ".join(sorted(known))
    fails: List[LintFailure] = []

    m = _block(content, "answer")
    if not m:
        return LintResult(False, [LintFailure("answer.missing_tag", "answer",
                                              "the response contains no <answer>...</answer> block")])
    lines = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
    if len(lines) != 4:
        fails.append(LintFailure("answer.line_count", "answer",
                                 f"the <answer> block must hold exactly 4 non-empty lines, one per group, but it has {len(lines)}"))
    for n, line in enumerate(lines, 1):
        label = _label(line)
        if label is not None:
            fails.append(LintFailure("answer.words_per_line", "answer",
                                     f'line {n} of the <answer> block, "{line}", is prefixed with the category label "{label}:"; '
                                     f'every line must be exactly four words, ALL CAPS, separated by commas, and nothing else'))
        elif len(_items(line)) != 4:
            fails.append(LintFailure("answer.words_per_line", "answer",
                                     f'line {n} of the <answer> block, "{line}", splits on commas into {len(_items(line))} item(s); '
                                     f'every line must be exactly four words, ALL CAPS, separated by commas'))
    if not fails:
        items = [w for line in lines for w in _items(line)]
        unknown = [w for w in items if w not in known]
        dups = sorted({w for w in items if items.count(w) > 1})
        missing = sorted(known - set(items))
        if unknown:
            fails.append(LintFailure("answer.unknown_word", "answer",
                                     f"these submitted words are not puzzle words: {', '.join(unknown)}. The 16 available words are: {available}"))
        if dups:
            fails.append(LintFailure("answer.duplicate_word", "answer",
                                     f"every puzzle word must be used exactly once, but these are used more than once: {', '.join(dups)}"))
        if missing:
            fails.append(LintFailure("answer.missing_word", "answer",
                                     f"every puzzle word must be used exactly once, but these are missing: {', '.join(missing)}"))

    t = _block(content, "traps")
    if t:
        tl = [ln.strip() for ln in t.group(1).splitlines() if ln.strip()]
        problem = None
        if not tl:
            problem = "the <traps> block is empty"
        elif _TRAP_SENTINEL.fullmatch(tl[0]):
            problem = None
        elif len(tl) != 1:
            problem = f"the <traps> block has {len(tl)} lines"
        elif _label(tl[0]) is not None:
            problem = f'the <traps> line, "{tl[0]}", is prefixed with a category label'
        elif len(_items(tl[0])) != 4:
            problem = f'the <traps> line, "{tl[0]}", splits on commas into {len(_items(tl[0]))} item(s)'
        elif any(w not in known for w in _items(tl[0])):
            problem = "these trap words are not puzzle words: " + ", ".join(w for w in _items(tl[0]) if w not in known)
        if problem:
            fails.append(LintFailure("traps.format", "traps",
                                     f"{problem}; it must hold ONE line with exactly four puzzle words, ALL CAPS, comma-separated, or N/A"))
    return LintResult(not fails, fails)


_XML_INSTRUCTION: Dict[str, str] = {
    "answer": "the complete <answer>...</answer> block (four lines, each with exactly four words, ALL CAPS, comma-separated) and nothing else",
    "traps": "the complete <traps>...</traps> block (ONE line with exactly four words, ALL CAPS, comma-separated, or N/A) and nothing else",
}
_JSON_INSTRUCTION: Dict[str, str] = {
    "answer": 'the "answer" value corrected: an array of exactly four arrays, each holding exactly four of the 16 puzzle words in ALL CAPS, every word used once, no category names or commentary inside the strings',
    "traps": 'the "traps" value corrected: an array of exactly four puzzle words in ALL CAPS, or an empty array if there is no trap',
}
_JSON_MISSING = ('the response was not a valid JSON object with an "answer" array (invalid JSON — check for '
                 'unescaped quotes, backslashes or line breaks inside strings — or the key was absent)')


def _json_terms(message: str) -> str:
    for tag in ("answer", "traps"):
        message = message.replace(f"<{tag}>...</{tag}> block", f'"{tag}" array')
        message = message.replace(f"<{tag}> block", f'"{tag}" array').replace(f"<{tag}> line", f'"{tag}" array')
    return message.replace("line ", "group ").replace(" lines", " groups")


def feedback_message(result: LintResult, protocol: str = "xml") -> str:
    """Model-facing text for a failed lint. Leads with the first failure; repeats
    of the same rule collapse to a count; other rules are appended.

    protocol "json" (structured output) asks for the whole JSON object with the
    key fixed, since the schema forbids a bare <answer> block."""
    if result.ok:
        return ""
    first, rest = result.failures[0], result.failures[1:]
    same = sum(1 for f in rest if f.rule == first.rule)
    other = [f for f in rest if f.rule != first.rule]
    if protocol == "json":
        msg = _JSON_MISSING if first.rule == "answer.missing_tag" else _json_terms(first.message)
        text = (f"Response failed linting rule {first.rule}: {msg}. Please re-submit the complete JSON object "
                f"required by the response schema with {_JSON_INSTRUCTION[first.segment]}. "
                f'Keep "thinking" to one short sentence.')
        unit = "group"
    else:
        text = (f"Response failed linting rule {first.rule}: {first.message}. "
                f"Please re-submit only the failed segment {first.segment}: {_XML_INSTRUCTION[first.segment]}.")
        unit = "line"
    if same:
        text += f" The same rule ({first.rule}) fails on {same} more {unit if same == 1 else unit + 's'}."
    if other:
        conv = _json_terms if protocol == "json" else (lambda s: s)
        text += " Also: " + "; ".join(f"{f.rule}: {conv(f.message)}" for f in other) + "."
    return text


def splice_segment(previous: str, resubmission: str, segment: str) -> str:
    """Merge a re-submitted block into the previous full response so the
    original <thinking> and a clean <traps> survive the repair. A tagless
    resubmission is wrapped in the segment's tags; anything unusable (empty,
    other markup) leaves `previous` unchanged so the same failure repeats."""
    new = _block(resubmission, segment)
    if new:
        block = new.group(0)
    else:
        body = strip_thinking(resubmission).strip()
        if not body or _ANY_TAG.search(body):
            return previous
        block = f"<{segment}>\n{body}\n</{segment}>"
    old = _block(previous, segment)
    if old:
        offset = len(previous) - len(strip_thinking(previous))  # _block matched on stripped text
        start, end = old.start() + offset, old.end() + offset
        if previous[start:end] == old.group(0):
            return previous[:start] + block + previous[end:]
        return previous.replace(old.group(0), block)
    return (previous.rstrip() + "\n\n" + block) if previous.strip() else block
