"""Tests for the shared ONESHOT result-string parser."""

from connections_eval.results import (
    API_ERROR,
    INVALID,
    SCORE,
    is_lint_retry,
    parse_oneshot_result,
)


def test_trap_scored_verdict():
    r = parse_oneshot_result("ONESHOT_SCORE_5_GROUPS_4_TRAP_2_MAX_5")
    assert r is not None
    assert (r.kind, r.score, r.groups, r.trap, r.max_score) == (SCORE, 5, 4, 2, 5)
    assert r.base == 3
    assert r.legacy is False


def test_unreviewed_puzzle_max_three():
    r = parse_oneshot_result("ONESHOT_SCORE_2_GROUPS_2_TRAP_0_MAX_3")
    assert (r.score, r.groups, r.trap, r.max_score, r.base) == (2, 2, 0, 3, 2)


def test_zero_score():
    r = parse_oneshot_result("ONESHOT_SCORE_0_GROUPS_0_TRAP_0_MAX_5")
    assert r.kind == SCORE
    assert (r.score, r.groups, r.base) == (0, 0, 0)


def test_legacy_bare_score_infers_groups_and_max():
    r = parse_oneshot_result("ONESHOT_SCORE_5")
    assert r.legacy is True
    assert (r.score, r.groups, r.trap, r.max_score) == (5, 4, 0, 5)
    # Legacy scores below the 4-group cap map straight through.
    assert parse_oneshot_result("ONESHOT_SCORE_2").groups == 2


def test_invalid_verdict():
    r = parse_oneshot_result("ONESHOT_INVALID_MAX_3")
    assert (r.kind, r.score, r.groups, r.max_score) == (INVALID, 0, 0, 3)


def test_api_error_verdict():
    r = parse_oneshot_result("ONESHOT_API_ERROR_MAX_5")
    assert (r.kind, r.score, r.max_score) == (API_ERROR, 0, 5)


def test_legacy_invalid_without_max():
    assert parse_oneshot_result("ONESHOT_INVALID").max_score == 5


def test_non_oneshot_results_are_none():
    for value in ("CORRECT", "INCORRECT_1", "INVALID_RESPONSE",
                  "LINT_RETRY_answer.words_per_line", "", None):
        assert parse_oneshot_result(value) is None


def test_lowercase_is_accepted():
    r = parse_oneshot_result("oneshot_score_3_groups_3_trap_0_max_5")
    assert (r.kind, r.score, r.groups) == (SCORE, 3, 3)


def test_is_lint_retry():
    assert is_lint_retry("LINT_RETRY_answer.line_count")
    assert not is_lint_retry("ONESHOT_SCORE_5_GROUPS_4_TRAP_2_MAX_5")
    assert not is_lint_retry(None)
