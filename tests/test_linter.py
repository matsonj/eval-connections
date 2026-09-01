"""Tests for the structural response linter."""

import pytest

from connections_eval.linter import feedback_message, lint_oneshot, splice_segment

WORDS = ["ARM", "WING", "FIN", "FLIPPER", "DODGE", "FORD", "LINCOLN", "RAM",
         "PLEATHER VEST", "MESH SHIRT", "EARRING", "NECKLACE",
         "FOUR-LETTER WORDS", "SWEARING", "PROFANITY", "EXPLETIVES"]
GOOD = ("ARM, WING, FIN, FLIPPER\nDODGE, FORD, LINCOLN, RAM\n"
        "PLEATHER VEST, MESH SHIRT, EARRING, NECKLACE\n"
        "FOUR-LETTER WORDS, SWEARING, PROFANITY, EXPLETIVES")


def answer(body=GOOD, traps=None, thinking=None):
    out = f"<thinking>{thinking}</thinking>\n" if thinking else ""
    out += f"<answer>\n{body}\n</answer>"
    if traps is not None:
        out += f"\n<traps>\n{traps}\n</traps>"
    return out + "\n<confidence>0.8</confidence>"


class TestRules:
    def test_clean_response_passes(self):
        assert lint_oneshot(answer(traps="N/A"), WORDS).ok
        assert lint_oneshot(answer(traps="ARM, DODGE, EARRING, SWEARING"), WORDS).ok

    def test_missing_tag_short_circuits(self):
        r = lint_oneshot("<thinking>only thinking</thinking>", WORDS)
        assert [f.rule for f in r.failures] == ["answer.missing_tag"]

    def test_decoy_answer_inside_thinking_is_ignored(self):
        r = lint_oneshot("<thinking>e.g. <answer>\nX, Y\n</answer></thinking>", WORDS)
        assert r.first_rule == "answer.missing_tag"

    def test_space_separated_lines_report_only_words_per_line(self):
        r = lint_oneshot(answer(GOOD.replace(",", "")), WORDS)
        assert {f.rule for f in r.failures} == {"answer.words_per_line"}
        assert len(r.failures) == 4
        assert 'line 1 of the <answer> block, "ARM WING FIN FLIPPER"' in r.failures[0].message

    def test_label_prefix_is_flagged_even_with_four_items(self):
        r = lint_oneshot(answer(GOOD.replace("ARM, WING", "LIMBS: ARM, WING", 1)), WORDS)
        assert r.first_rule == "answer.words_per_line"
        assert 'category label "LIMBS:"' in r.failures[0].message

    def test_line_count(self):
        r = lint_oneshot(answer("\n".join(GOOD.splitlines()[:3])), WORDS)
        assert r.first_rule == "answer.line_count"

    @pytest.mark.parametrize("swap, rule", [
        (("FLIPPER", "FLIPPERS"), "answer.unknown_word"),
        (("FLIPPER", "ARM"), "answer.duplicate_word"),
    ])
    def test_word_rules(self, swap, rule):
        r = lint_oneshot(answer(GOOD.replace(*swap, 1)), WORDS)
        rules = [f.rule for f in r.failures]
        assert rules[0] == rule and "answer.missing_word" in rules

    def test_multi_word_and_hyphenated_puzzle_words_pass(self):
        assert lint_oneshot(answer(GOOD.lower()), WORDS).ok

    def test_traps_missing_is_not_a_failure_but_malformed_is(self):
        assert lint_oneshot(answer(), WORDS).ok
        for bad in ("", "ARM DODGE EARRING SWEARING", "ARM, DODGE", "A, B, C, D",
                    "ARM, DODGE, EARRING, SWEARING\nFIN, FORD, MESH SHIRT, PROFANITY"):
            r = lint_oneshot(answer(traps=bad), WORDS)
            assert [f.rule for f in r.failures] == ["traps.format"], bad


class TestFeedback:
    def test_xml_wording_collapses_repeated_rule(self):
        text = feedback_message(lint_oneshot(answer(GOOD.replace(",", "")), WORDS))
        assert text.startswith("Response failed linting rule answer.words_per_line:")
        assert "re-submit only the failed segment answer: the complete <answer>...</answer> block" in text
        assert "fails on 3 more lines" in text
        assert text.count('splits on commas') == 1

    def test_other_rules_are_appended(self):
        r = lint_oneshot(answer(GOOD.replace("FLIPPER", "ARM", 1), traps="A B"), WORDS)
        text = feedback_message(r)
        assert "Also: answer.missing_word" in text and "traps.format" in text

    def test_json_wording_asks_for_the_whole_object(self):
        text = feedback_message(lint_oneshot("not json", WORDS), "json")
        assert "valid JSON object" in text and "<answer>" not in text
        assert 'complete JSON object' in text and '"answer" value corrected' in text
        text = feedback_message(lint_oneshot(answer(GOOD.replace(",", "")), WORDS), "json")
        assert '"answer" array' in text and "group 1" in text and "3 more groups" in text

    def test_passing_result_gives_empty_string(self):
        assert feedback_message(lint_oneshot(answer(), WORDS)) == ""


class TestSplice:
    def test_tagged_block_replaces_old_and_keeps_thinking_and_traps(self):
        prev = answer("A B C D", traps="N/A", thinking="my reasoning")
        merged = splice_segment(prev, f"<answer>\n{GOOD}\n</answer>", "answer")
        assert lint_oneshot(merged, WORDS).ok
        assert "my reasoning" in merged and "<traps>\nN/A\n</traps>" in merged
        assert merged.count("<answer>") == 1

    def test_bare_lines_are_wrapped(self):
        merged = splice_segment(answer("A B C D"), GOOD, "answer")
        assert lint_oneshot(merged, WORDS).ok

    def test_missing_block_is_appended(self):
        merged = splice_segment("<thinking>none</thinking>", GOOD, "answer")
        assert lint_oneshot(merged, WORDS).ok and "none" in merged

    def test_decoy_in_thinking_is_not_the_one_replaced(self):
        prev = answer("A B C D", thinking="see <answer>\nX, Y\n</answer>")
        merged = splice_segment(prev, f"<answer>\n{GOOD}\n</answer>", "answer")
        assert lint_oneshot(merged, WORDS).ok and "X, Y" in merged

    def test_unusable_resubmission_keeps_previous(self):
        prev = answer("A B C D")
        assert splice_segment(prev, "<confidence>0.4</confidence>", "answer") == prev
        assert splice_segment(prev, "  ", "answer") == prev

    def test_traps_segment(self):
        merged = splice_segment(answer(traps="ARM DODGE"), "ARM, DODGE, EARRING, SWEARING", "traps")
        assert lint_oneshot(merged, WORDS).ok and merged.count("<traps>") == 1
