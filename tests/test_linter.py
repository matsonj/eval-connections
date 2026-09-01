"""Tests for the structural response linter.

The word list deliberately mixes plain words with the awkward canonical shapes
the real puzzle file contains — a two-word entry (PLEATHER VEST), a hyphenated
one (FOUR-LETTER WORDS), and punctuation (N.F.L., GREEK/ROMAN GOD) — because a
lenient tokenizer would mangle exactly those, and the linter must not.
"""

import pytest

from connections_eval.linter import (
    LintFailure,
    LintResult,
    feedback_message,
    lint_classic,
    lint_oneshot,
    parse_guess_words,
    splice_segment,
)


WORDS = [
    "ARM", "WING", "FIN", "FLIPPER",
    "DODGE", "FORD", "LINCOLN", "RAM",
    "DUCK", "EVADE", "SIDESTEP", "AVOID",
    "PLEATHER VEST", "FOUR-LETTER WORDS", "N.F.L.", "GREEK/ROMAN GOD",
]

GOOD_ANSWER_BODY = (
    "ARM, WING, FIN, FLIPPER\n"
    "DODGE, FORD, LINCOLN, RAM\n"
    "DUCK, EVADE, SIDESTEP, AVOID\n"
    "PLEATHER VEST, FOUR-LETTER WORDS, N.F.L., GREEK/ROMAN GOD"
)


def answer(body=GOOD_ANSWER_BODY, traps=None, thinking=None, confidence=None):
    """Assemble a one-shot response in the shape the prompt template asks for."""
    parts = []
    if thinking is not None:
        parts.append(f"<thinking>\n{thinking}\n</thinking>")
    parts.append(f"<answer>\n{body}\n</answer>")
    if traps is not None:
        parts.append(f"<traps>\n{traps}\n</traps>")
    if confidence is not None:
        parts.append(f"<confidence>\n{confidence}\n</confidence>")
    return "\n".join(parts)


def rules(result):
    return [f.rule for f in result.failures]


class TestOneshotAnswerRules:
    def test_well_formed_answer_passes(self):
        assert lint_oneshot(answer(), WORDS).ok

    def test_multi_word_and_punctuated_words_pass(self):
        """Comma-separated multi-word entries must not be mistaken for extra words."""
        result = lint_oneshot(answer(), WORDS)
        assert result.ok, feedback_message(result)

    def test_a_wrong_but_well_formed_answer_still_passes(self):
        """The linter is structural only — it must never judge correctness, or the
        feedback would leak the solution back to the model mid-eval."""
        scrambled = (
            "ARM, DODGE, DUCK, PLEATHER VEST\n"
            "WING, FORD, EVADE, FOUR-LETTER WORDS\n"
            "FIN, LINCOLN, SIDESTEP, N.F.L.\n"
            "FLIPPER, RAM, AVOID, GREEK/ROMAN GOD"
        )
        assert lint_oneshot(answer(scrambled), WORDS).ok

    def test_missing_answer_tag(self):
        result = lint_oneshot("Here are my groups:\nARM, WING, FIN, FLIPPER", WORDS)
        assert rules(result) == ["answer.missing_tag"]
        assert result.failures[0].segment == "answer"

    def test_decoy_answer_inside_thinking_is_ignored(self):
        """A worked example in the reasoning block must not satisfy the rule."""
        content = "<thinking>\n<answer>\nA, B, C, D\n</answer>\n</thinking>\nNo real answer."
        assert rules(lint_oneshot(content, WORDS)) == ["answer.missing_tag"]

    def test_decoy_answer_inside_thinking_does_not_shadow_the_real_one(self):
        content = answer(thinking="I might write <answer>\nA, B, C, D\n</answer> here")
        assert lint_oneshot(content, WORDS).ok

    def test_unclosed_thinking_block_swallows_the_rest(self):
        """Truncated reasoning leaves no answer at all — same as core's parser."""
        content = "<thinking>\nstill reasoning\n<answer>\nARM, WING, FIN, FLIPPER\n"
        assert rules(lint_oneshot(content, WORDS)) == ["answer.missing_tag"]

    @pytest.mark.parametrize("body,count", [
        ("ARM, WING, FIN, FLIPPER\nDODGE, FORD, LINCOLN, RAM", 2),
        (GOOD_ANSWER_BODY + "\nARM, WING, FIN, FLIPPER", 5),
    ])
    def test_line_count(self, body, count):
        result = lint_oneshot(answer(body), WORDS)
        assert "answer.line_count" in rules(result)
        assert f"has {count}" in result.failures[0].message

    def test_blank_lines_inside_the_block_are_not_counted(self):
        body = GOOD_ANSWER_BODY.replace("\n", "\n\n")
        assert lint_oneshot(answer(body), WORDS).ok

    def test_space_separated_line_is_words_per_line(self):
        """The real granite-4.2-8b failure: spaces instead of commas."""
        body = GOOD_ANSWER_BODY.replace("ARM, WING, FIN, FLIPPER", "ARM WING FIN FLIPPER")
        result = lint_oneshot(answer(body), WORDS)

        assert rules(result) == ["answer.words_per_line"]
        message = result.failures[0].message
        assert 'line 1' in message
        assert '"ARM WING FIN FLIPPER"' in message
        assert "into 1 item(s)" in message
        assert "exactly four words, ALL CAPS, separated by commas" in message

    def test_label_prefixed_line_is_words_per_line(self):
        """`CAR BRANDS: DODGE, FORD, LINCOLN, RAM` splits into four items, so only
        the label itself gives it away."""
        body = GOOD_ANSWER_BODY.replace(
            "DODGE, FORD, LINCOLN, RAM", "CAR BRANDS: DODGE, FORD, LINCOLN, RAM")
        result = lint_oneshot(answer(body), WORDS)

        assert rules(result) == ["answer.words_per_line"]
        assert 'line 2' in result.failures[0].message
        assert '"CAR BRANDS:"' in result.failures[0].message

    def test_five_words_on_one_line_is_words_per_line(self):
        body = GOOD_ANSWER_BODY.replace(
            "ARM, WING, FIN, FLIPPER", "ARM, WING, FIN, FLIPPER, DUCK")
        result = lint_oneshot(answer(body), WORDS)
        assert "answer.words_per_line" in rules(result)
        assert "into 5 item(s)" in result.failures[0].message

    def test_word_rules_are_suppressed_while_lines_are_malformed(self):
        """A space-separated block would otherwise report all 16 words unknown and
        all 16 missing, burying the one rule the model can act on."""
        body = "\n".join(line.replace(", ", " ") for line in GOOD_ANSWER_BODY.splitlines())
        result = lint_oneshot(answer(body), WORDS)
        assert set(rules(result)) == {"answer.words_per_line"}

    def test_unknown_word(self):
        body = GOOD_ANSWER_BODY.replace("FLIPPER", "TENTACLE")
        result = lint_oneshot(answer(body), WORDS)

        assert "answer.unknown_word" in rules(result)
        assert "TENTACLE" in result.failures[0].message
        # The available words are restated so the model can self-correct
        assert "PLEATHER VEST" in result.failures[0].message
        # ...and the swapped-out word is reported missing too
        assert "answer.missing_word" in rules(result)

    def test_duplicate_and_missing_word(self):
        body = GOOD_ANSWER_BODY.replace("EVADE", "DUCK")
        result = lint_oneshot(answer(body), WORDS)

        assert rules(result) == ["answer.duplicate_word", "answer.missing_word"]
        assert "DUCK" in result.failures[0].message
        assert "EVADE" in result.failures[1].message

    def test_lowercase_words_are_accepted_by_the_word_rules(self):
        """Case is normalised before comparison; ALL CAPS is asked for in the
        message, not enforced as a separate failure."""
        assert lint_oneshot(answer(GOOD_ANSWER_BODY.lower()), WORDS).ok


class TestOneshotTrapRules:
    def test_missing_traps_block_is_not_a_failure(self):
        """Omitting traps forfeits the bonus; that is a scoring outcome, not a
        format error, and must not burn a retry."""
        assert lint_oneshot(answer(), WORDS).ok

    @pytest.mark.parametrize("claim", ["N/A", "n/a", "  N/A  ", "NA", "none", "N/A."])
    def test_na_traps_pass(self, claim):
        assert lint_oneshot(answer(traps=claim), WORDS).ok

    def test_valid_trap_line_passes(self):
        assert lint_oneshot(answer(traps="ARM, DODGE, DUCK, N.F.L."), WORDS).ok

    def test_space_separated_trap_line_fails(self):
        result = lint_oneshot(answer(traps="ARM DODGE DUCK RAM"), WORDS)
        assert rules(result) == ["traps.format"]
        assert result.failures[0].segment == "traps"

    def test_trap_line_with_unknown_word_fails(self):
        result = lint_oneshot(answer(traps="ARM, DODGE, DUCK, TENTACLE"), WORDS)
        assert rules(result) == ["traps.format"]
        assert "TENTACLE" in result.failures[0].message

    def test_multi_line_traps_fail(self):
        result = lint_oneshot(answer(traps="ARM, DODGE, DUCK, RAM\nFIN, FORD, EVADE, WING"),
                              WORDS)
        assert rules(result) == ["traps.format"]
        assert "exactly one line" in result.failures[0].message

    def test_empty_traps_block_fails(self):
        result = lint_oneshot(answer(traps="   "), WORDS)
        assert rules(result) == ["traps.format"]
        assert "empty" in result.failures[0].message

    def test_answer_failure_leads_over_a_traps_failure(self):
        body = GOOD_ANSWER_BODY.replace("ARM, WING, FIN, FLIPPER", "ARM WING FIN FLIPPER")
        result = lint_oneshot(answer(body, traps="ARM DODGE DUCK RAM"), WORDS)
        assert rules(result) == ["answer.words_per_line", "traps.format"]

    def test_confidence_is_never_linted(self):
        assert lint_oneshot(answer(confidence="not a number at all"), WORDS).ok


class TestFeedbackMessage:
    def test_passing_result_renders_nothing(self):
        assert feedback_message(LintResult(ok=True)) == ""

    def test_names_rule_and_segment(self):
        body = GOOD_ANSWER_BODY.replace("ARM, WING, FIN, FLIPPER", "ARM WING FIN FLIPPER")
        text = feedback_message(lint_oneshot(answer(body), WORDS))

        assert text.startswith("Response failed linting rule answer.words_per_line: ")
        assert "Please re-submit only the failed segment answer: " in text
        assert "<answer>...</answer>" in text
        assert "and nothing else." in text

    def test_extra_failures_are_appended(self):
        body = GOOD_ANSWER_BODY.replace("FLIPPER", "TENTACLE")
        text = feedback_message(lint_oneshot(answer(body), WORDS))
        assert " Also: answer.missing_word: " in text

    def test_traps_instruction_differs_from_answer(self):
        text = feedback_message(lint_oneshot(answer(traps="ARM DODGE DUCK RAM"), WORDS))
        assert "failed segment traps" in text
        assert "ONE line" in text and "or N/A" in text

    def test_segment_hint_overrides_the_failure_segment(self):
        result = LintResult(ok=False, failures=[LintFailure("x.y", "answer", "bad")])
        assert "failed segment guess" in feedback_message(result, segment_hint="guess")

    def test_no_double_period_after_the_rule_message(self):
        result = LintResult(ok=False, failures=[LintFailure("x.y", "answer", "bad.")])
        assert "rule x.y: bad. Please" in feedback_message(result)


class TestClassicRules:
    SOLVED = {"ARM", "WING", "FIN", "FLIPPER"}

    def test_well_formed_guess_passes(self):
        assert lint_classic("<guess>DODGE, FORD, LINCOLN, RAM</guess>", WORDS, set()).ok

    def test_multi_word_entries_pass(self):
        content = "<guess>PLEATHER VEST, FOUR-LETTER WORDS, N.F.L., GREEK/ROMAN GOD</guess>"
        assert lint_classic(content, WORDS, set()).ok

    def test_missing_guess_tag(self):
        result = lint_classic("I think it is DODGE, FORD, LINCOLN, RAM", WORDS, set())
        assert rules(result) == ["guess.missing_tag"]
        assert result.failures[0].segment == "guess"

    def test_decoy_guess_inside_thinking_is_ignored(self):
        content = "<thinking><guess>ARM, WING, FIN, FLIPPER</guess></thinking>"
        assert "guess.missing_tag" in rules(lint_classic(content, WORDS, set()))

    def test_word_count(self):
        result = lint_classic("<guess>DODGE, FORD, LINCOLN</guess>", WORDS, set())
        assert rules(result) == ["guess.word_count"]
        assert "3 were found" in result.failures[0].message
        assert "Available words:" in result.failures[0].message

    def test_space_separated_guess_is_word_count(self):
        result = lint_classic("<guess>DODGE FORD LINCOLN RAM</guess>", WORDS, set())
        assert rules(result) == ["guess.word_count"]
        assert "1 were found" in result.failures[0].message

    def test_unknown_word(self):
        result = lint_classic("<guess>DODGE, FORD, LINCOLN, TESLA</guess>", WORDS, set())
        assert rules(result) == ["guess.unknown_word"]
        assert "TESLA" in result.failures[0].message

    def test_duplicate_word(self):
        result = lint_classic("<guess>DODGE, DODGE, LINCOLN, RAM</guess>", WORDS, set())
        assert rules(result) == ["guess.duplicate_word"]
        assert "DODGE" in result.failures[0].message

    def test_solved_word(self):
        result = lint_classic("<guess>ARM, FORD, LINCOLN, RAM</guess>", WORDS, self.SOLVED)
        assert rules(result) == ["guess.solved_word"]
        assert "ARM" in result.failures[0].message
        # Solved words drop out of the available list
        assert "WING" not in result.failures[0].message.split("Available words:")[1]

    def test_available_words_exclude_solved_groups(self):
        result = lint_classic("<guess>DODGE, FORD</guess>", WORDS, self.SOLVED)
        available = result.failures[0].message.split("Available words:")[1]
        assert "FLIPPER" not in available
        assert "SIDESTEP" in available


class TestParseGuessWords:
    def test_guess_block_wins(self):
        assert parse_guess_words("<guess>a, b</guess>\nc, d") == ["A", "B"]

    def test_caps_fallback(self):
        assert parse_guess_words("My guess: DODGE, FORD, LINCOLN, RAM") == [
            "DODGE", "FORD", "LINCOLN", "RAM"]

    def test_comma_fallback(self):
        assert parse_guess_words("dodge, ford") == ["DODGE", "FORD"]


class TestSpliceSegment:
    def test_tagged_resubmission_replaces_the_old_block(self):
        previous = answer("A B C D", traps="N/A")
        merged = splice_segment(previous, f"<answer>\n{GOOD_ANSWER_BODY}\n</answer>", "answer")

        assert lint_oneshot(merged, WORDS).ok
        assert "<traps>" in merged  # the clean trap claim survives the repair

    def test_bare_lines_are_wrapped_before_splicing(self):
        previous = answer("A B C D")
        merged = splice_segment(previous, GOOD_ANSWER_BODY, "answer")
        assert lint_oneshot(merged, WORDS).ok

    def test_missing_block_is_appended(self):
        previous = "<thinking>\nno answer emitted\n</thinking>"
        merged = splice_segment(previous, GOOD_ANSWER_BODY, "answer")

        assert lint_oneshot(merged, WORDS).ok
        assert "no answer emitted" in merged

    def test_decoy_block_inside_thinking_is_not_the_one_replaced(self):
        previous = answer("A B C D", thinking="example <answer>\nX, Y\n</answer>")
        merged = splice_segment(previous, f"<answer>\n{GOOD_ANSWER_BODY}\n</answer>", "answer")

        assert lint_oneshot(merged, WORDS).ok
        assert "X, Y" in merged  # the reasoning is left untouched

    def test_unusable_resubmission_keeps_the_previous_content(self):
        previous = answer("A B C D")
        assert splice_segment(previous, "<confidence>0.4</confidence>", "answer") == previous
        assert splice_segment(previous, "   ", "answer") == previous

    def test_traps_segment_is_spliced_independently(self):
        previous = answer(traps="ARM DODGE DUCK RAM")
        merged = splice_segment(previous, "ARM, DODGE, DUCK, RAM", "traps")

        assert lint_oneshot(merged, WORDS).ok
        assert merged.count("<traps>") == 1
