"""Tests for core game logic."""

import random
import time
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from connections_eval import core as core_mod
from connections_eval.core import ConnectionsGame, GameState, PuzzleResult, EvalStats
from connections_eval.adapters import openrouter_adapter
from connections_eval.adapters.openrouter_adapter import extract_provider_slug
from connections_eval.utils.tokens import extract_cache_info

from tests.conftest import INPUTS, make_test_groups, make_puzzle, make_response


def _base_score(game, puzzle, groups):
    """(groups_correct, base_score) from the single-pass one-shot grader."""
    _, matched, base = game._grade_oneshot(puzzle, groups)
    return (len(matched), base)


def _ok_response(content, **response_kwargs):
    """A MagicMock like requests.post()'s return value for a 200 OK chat
    completion, wrapping make_response for the JSON body."""
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.json.return_value = make_response(content, **response_kwargs)
    mock_response.raise_for_status.return_value = None
    return mock_response


class TestConnectionsGame:
    """Test ConnectionsGame class."""

    @pytest.fixture
    def game_state(self, sample_puzzle):
        """Create a sample game state."""
        return GameState(puzzle=sample_puzzle)

    @pytest.mark.parametrize("response, expected", [
        pytest.param("APPLE, BANANA, CHERRY, GRAPE",
                     ["APPLE", "BANANA", "CHERRY", "GRAPE"], id="normal"),
        pytest.param("apple, Banana, CHERRY, grape",
                     ["APPLE", "BANANA", "CHERRY", "GRAPE"], id="mixed_case"),
        pytest.param(" APPLE , BANANA,  CHERRY , GRAPE ",
                     ["APPLE", "BANANA", "CHERRY", "GRAPE"], id="extra_whitespace"),
        pytest.param("APPLE, BANANA, CHERRY",
                     ["APPLE", "BANANA", "CHERRY"], id="wrong_number_of_words"),
    ])
    def test_parse_response(self, mock_game, response, expected):
        """Test response parsing."""
        assert mock_game._parse_response(response) == expected

    def test_validate_guess_correct(self, mock_game, game_state):
        """Test validation of correct guess."""
        # Valid guess
        words = ["APPLE", "BANANA", "CHERRY", "GRAPE"]
        error = mock_game._validate_guess(game_state, words)
        assert error is None

    def test_validate_guess_wrong_count(self, mock_game, game_state):
        """Test validation with wrong word count."""
        # Too few words
        words = ["APPLE", "BANANA", "CHERRY"]
        error = mock_game._validate_guess(game_state, words)
        assert "Expected 4 words, got 3" in error

        # Too many words
        words = ["APPLE", "BANANA", "CHERRY", "GRAPE", "BLUE"]
        error = mock_game._validate_guess(game_state, words)
        assert "Expected 4 words, got 5" in error

    def test_validate_guess_duplicates(self, mock_game, game_state):
        """Test validation with duplicate words."""
        words = ["APPLE", "APPLE", "CHERRY", "GRAPE"]
        error = mock_game._validate_guess(game_state, words)
        assert "Duplicate words not allowed" in error

    def test_validate_guess_invalid_word(self, mock_game, game_state):
        """Test validation with word not in puzzle."""
        words = ["APPLE", "BANANA", "CHERRY", "ORANGE"]
        error = mock_game._validate_guess(game_state, words)
        assert "Word 'ORANGE' not in puzzle" in error

    def test_validate_guess_solved_group(self, mock_game, game_state):
        """Test validation with word from solved group."""
        # Mark green group as solved
        game_state.solved_groups.add("green")

        words = ["APPLE", "BLUE", "FAST", "BRIGHT"]
        error = mock_game._validate_guess(game_state, words)
        assert "Word 'APPLE' is from an already solved group" in error

    def test_process_guess_correct(self, mock_game, game_state):
        """Test processing correct guess."""
        # Correct guess for fruits group
        result = mock_game._process_guess(game_state, "APPLE, BANANA, CHERRY, GRAPE")

        assert result == "CORRECT. NEXT GUESS?"
        assert game_state.guess_count == 1
        assert game_state.mistake_count == 0
        assert "green" in game_state.solved_groups
        assert not game_state.finished  # Not all groups solved

    def test_process_guess_incorrect(self, mock_game, game_state):
        """Test processing incorrect guess."""
        # Mix of different groups
        result = mock_game._process_guess(game_state, "APPLE, BLUE, FAST, BRIGHT")

        assert result == "INCORRECT. 3 INCORRECT GUESSES REMAINING."
        assert game_state.guess_count == 1
        assert game_state.mistake_count == 1
        assert len(game_state.solved_groups) == 0
        assert not game_state.finished

    def test_process_guess_invalid(self, mock_game, game_state):
        """Test processing invalid guess."""
        # Wrong number of words
        result = mock_game._process_guess(game_state, "APPLE, BANANA, CHERRY")

        assert result.startswith("INVALID_RESPONSE")
        assert "Expected 4 words, got 3" in result
        assert "Available words:" in result
        assert game_state.guess_count == 0  # Invalid guess doesn't count
        assert game_state.invalid_count == 1
        assert not game_state.finished

    def test_game_win_condition(self, mock_game, game_state):
        """Test game win condition."""
        # Solve all 4 groups
        for i, group in enumerate(game_state.puzzle.groups):
            words_str = ", ".join(group.words)
            result = mock_game._process_guess(game_state, words_str)

            if i == 3:  # Last group
                assert result == "CORRECT"
                assert game_state.finished
                assert game_state.won
            else:
                assert result == "CORRECT. NEXT GUESS?"
                assert not game_state.finished

    def test_game_lose_condition_mistakes(self, mock_game, game_state):
        """Test game lose condition from too many mistakes."""
        # Make 4 incorrect guesses
        for i in range(4):
            result = mock_game._process_guess(game_state, "APPLE, BLUE, FAST, BRIGHT")
            expected_remaining = 3 - i
            assert result == f"INCORRECT. {expected_remaining} INCORRECT GUESSES REMAINING."

            if i == 3:  # 4th mistake
                assert game_state.finished
                assert not game_state.won
            else:
                assert not game_state.finished

    def test_game_lose_condition_invalid(self, mock_game, game_state):
        """Test game lose condition from too many invalid responses."""
        # Make 3 invalid guesses
        for i in range(3):
            result = mock_game._process_guess(game_state, "APPLE, BANANA")  # Wrong count
            assert result.startswith("INVALID_RESPONSE")

            if i == 2:  # 3rd invalid
                assert game_state.finished
                assert not game_state.won
            else:
                assert not game_state.finished

    def test_render_prompt_template(self, mock_game):
        """Test prompt template rendering."""
        mock_game.prompt_template = "Puzzle {{PUZZLE_ID}} difficulty {{DIFFICULTY}}: {{WORDS}}"

        words = ["APPLE", "BANANA", "CHERRY", "GRAPE"]
        result = mock_game._render_prompt_template(477, 3.8, words)

        expected = "Puzzle 477 difficulty 3.8: APPLE, BANANA, CHERRY, GRAPE"
        assert result == expected

    def test_puzzle_canonical_default(self, sample_puzzle):
        """Test that puzzle canonical defaults to False."""
        assert sample_puzzle.canonical is False

    def test_puzzle_canonical_true(self, canonical_puzzle):
        """Test that canonical puzzle has canonical=True."""
        assert canonical_puzzle.canonical is True

    def test_get_canonical_puzzle_ids(self, mock_game, sample_puzzle, canonical_puzzle):
        """Test getting canonical puzzle IDs."""
        mock_game.puzzles = [sample_puzzle, canonical_puzzle]
        canonical_ids = mock_game.get_canonical_puzzle_ids()
        assert canonical_ids == [999]

    def test_get_canonical_puzzle_ids_empty(self, mock_game, sample_puzzle):
        """Test getting canonical puzzle IDs when none are canonical."""
        mock_game.puzzles = [sample_puzzle]
        canonical_ids = mock_game.get_canonical_puzzle_ids()
        assert canonical_ids == []

    def test_accumulate_stats(self):
        """Test EvalStats accumulation from PuzzleResult."""
        stats = EvalStats()
        result = PuzzleResult(
            won=True,
            guess_count=4,
            mistake_count=0,
            invalid_count=0,
            solved_groups=["green", "yellow", "blue", "purple"],
            time_sec=10.5,
            total_tokens=1000,
            total_prompt_tokens=800,
            total_completion_tokens=200,
            token_count_method="API",
            total_cost=0.01,
            total_upstream_cost=0.005,
        )
        stats.accumulate(result)

        assert stats.puzzles_attempted == 1
        assert stats.puzzles_solved == 1
        assert stats.total_guesses == 4
        assert stats.correct_guesses == 4
        assert stats.total_tokens == 1000
        assert stats.token_count_method == "API"


class TestOneshotParsing:
    """Test _parse_oneshot_response for one-shot mode."""

    _WELL_FORMED_ANSWER = """<answer>
APPLE, BANANA, CHERRY, GRAPE
BLUE, GREEN, RED, YELLOW
FAST, QUICK, RAPID, SWIFT
BRIGHT, CLEVER, SMART, WISE
</answer>"""

    _EXPECTED_GROUPS = [
        ["APPLE", "BANANA", "CHERRY", "GRAPE"],
        ["BLUE", "GREEN", "RED", "YELLOW"],
        ["FAST", "QUICK", "RAPID", "SWIFT"],
        ["BRIGHT", "CLEVER", "SMART", "WISE"],
    ]

    def test_well_formed_answer_block(self, mock_game):
        """A properly formatted <answer> block with 4 lines parses into 4 groups of 4."""
        groups = mock_game._parse_oneshot_response(self._WELL_FORMED_ANSWER)
        assert groups == self._EXPECTED_GROUPS

    def test_thinking_block_with_decoy_answer_is_stripped(self, mock_game):
        """A decoy <answer> example inside <thinking> must not be picked up; the
        real answer after the thinking block should be used instead."""
        response = f"""<thinking>
Here's an example of the expected format:
<answer>
DECOY, DECOY, DECOY, DECOY
DECOY, DECOY, DECOY, DECOY
DECOY, DECOY, DECOY, DECOY
DECOY, DECOY, DECOY, DECOY
</answer>
Now here is my actual reasoning about the puzzle...
</thinking>
{self._WELL_FORMED_ANSWER}"""
        groups = mock_game._parse_oneshot_response(response)
        assert groups == self._EXPECTED_GROUPS

    def test_unclosed_think_tag_strips_to_end(self, mock_game):
        """An unclosed <think> tag (truncated response) strips everything from
        that point to the end of the string, including any decoy answer."""
        response = """<think>
This reasoning never closes...
<answer>
DECOY, DECOY, DECOY, DECOY
</answer>
"""
        groups = mock_game._parse_oneshot_response(response)
        assert groups == []

    def test_fallback_no_answer_tag_plain_caps_lines(self, mock_game):
        """When there's no <answer> tag, fall back to scanning for lines of 4
        comma-separated ALL CAPS words."""
        response = """I'll just list them plainly below.
APPLE, BANANA, CHERRY, GRAPE
BLUE, GREEN, RED, YELLOW
FAST, QUICK, RAPID, SWIFT
BRIGHT, CLEVER, SMART, WISE
Done."""
        groups = mock_game._parse_oneshot_response(response)
        assert groups == self._EXPECTED_GROUPS

    def test_garbage_input_fails_scoring(self, mock_game, sample_puzzle):
        """Garbage input may parse into something, but it must fail scoring."""
        groups = mock_game._parse_oneshot_response("This is just garbage nonsense text with no structure.")
        assert _base_score(mock_game, sample_puzzle, groups) == (0, 0)

    def test_lowercase_words_are_upper_cased(self, mock_game):
        """Lowercase words in the answer block are normalized to upper case."""
        response = """<answer>
apple, banana, cherry, grape
blue, green, red, yellow
fast, quick, rapid, swift
bright, clever, smart, wise
</answer>"""
        groups = mock_game._parse_oneshot_response(response)
        assert groups == self._EXPECTED_GROUPS


class TestOneshotScoring:
    """Test one-shot base scoring (_grade_oneshot)."""

    def test_all_four_correct(self, mock_game, sample_puzzle):
        groups = [
            ["APPLE", "BANANA", "CHERRY", "GRAPE"],
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
            ["BRIGHT", "CLEVER", "SMART", "WISE"],
        ]
        assert _base_score(mock_game, sample_puzzle, groups) == (4, 3)

    def test_two_correct_two_swapped(self, mock_game, sample_puzzle):
        """Two groups intact; the other two have words swapped between them so
        neither matches any puzzle group."""
        groups = [
            ["APPLE", "BANANA", "CHERRY", "GRAPE"],   # correct (Fruits)
            ["BLUE", "GREEN", "RED", "YELLOW"],       # correct (Colors)
            ["FAST", "QUICK", "SMART", "WISE"],       # 2 from Speed + 2 from Smart
            ["RAPID", "SWIFT", "BRIGHT", "CLEVER"],   # remaining 2 from Speed + 2 from Smart
        ]
        assert _base_score(mock_game, sample_puzzle, groups) == (2, 2)

    def test_one_correct(self, mock_game, sample_puzzle):
        """One group intact; the other 12 words are 3-cycled across the
        remaining three groups so none of them matches any puzzle group."""
        groups = [
            ["APPLE", "BANANA", "CHERRY", "GRAPE"],   # correct (Fruits)
            ["GREEN", "RED", "YELLOW", "BRIGHT"],     # Colors minus BLUE, plus BRIGHT
            ["QUICK", "RAPID", "SWIFT", "BLUE"],      # Speed minus FAST, plus BLUE
            ["CLEVER", "SMART", "WISE", "FAST"],      # Smart minus BRIGHT, plus FAST
        ]
        assert _base_score(mock_game, sample_puzzle, groups) == (1, 1)

    def test_zero_correct_valid_partition(self, mock_game, sample_puzzle):
        """A full derangement: every group has exactly one word swapped in from
        the cyclically-next group, so all 16 words are used once but no
        submitted group matches any puzzle group."""
        groups = [
            ["BLUE", "BANANA", "CHERRY", "GRAPE"],
            ["FAST", "GREEN", "RED", "YELLOW"],
            ["BRIGHT", "QUICK", "RAPID", "SWIFT"],
            ["APPLE", "CLEVER", "SMART", "WISE"],
        ]
        assert _base_score(mock_game, sample_puzzle, groups) == (0, 0)

    def test_wrong_word_not_in_puzzle(self, mock_game, sample_puzzle):
        """A word that doesn't belong to the puzzle at all is a structural failure."""
        groups = [
            ["APPLE", "BANANA", "CHERRY", "ORANGE"],  # ORANGE isn't in the puzzle
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
            ["BRIGHT", "CLEVER", "SMART", "WISE"],
        ]
        assert _base_score(mock_game, sample_puzzle, groups) == (0, 0)

    def test_duplicate_word_one_missing(self, mock_game, sample_puzzle):
        """A word appearing twice (with another word missing) is a structural failure."""
        groups = [
            ["APPLE", "APPLE", "CHERRY", "GRAPE"],  # APPLE duplicated, BANANA missing
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
            ["BRIGHT", "CLEVER", "SMART", "WISE"],
        ]
        assert _base_score(mock_game, sample_puzzle, groups) == (0, 0)

    def test_three_groups_only(self, mock_game, sample_puzzle):
        groups = [
            ["APPLE", "BANANA", "CHERRY", "GRAPE"],
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
        ]
        assert _base_score(mock_game, sample_puzzle, groups) == (0, 0)

    def test_five_groups(self, mock_game, sample_puzzle):
        groups = [
            ["APPLE", "BANANA", "CHERRY", "GRAPE"],
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
            ["BRIGHT", "CLEVER", "SMART", "WISE"],
            ["EXTRA", "GROUP", "NOT", "ALLOWED"],
        ]
        assert _base_score(mock_game, sample_puzzle, groups) == (0, 0)

    def test_group_with_three_words(self, mock_game, sample_puzzle):
        groups = [
            ["APPLE", "BANANA", "CHERRY"],  # only 3 words
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
            ["BRIGHT", "CLEVER", "SMART", "WISE"],
        ]
        assert _base_score(mock_game, sample_puzzle, groups) == (0, 0)

    def test_word_order_within_group_irrelevant(self, mock_game, sample_puzzle):
        """Groups are compared as sets, so word order within a group doesn't matter."""
        groups = [
            ["GRAPE", "CHERRY", "BANANA", "APPLE"],
            ["YELLOW", "RED", "GREEN", "BLUE"],
            ["SWIFT", "RAPID", "QUICK", "FAST"],
            ["WISE", "SMART", "CLEVER", "BRIGHT"],
        ]
        assert _base_score(mock_game, sample_puzzle, groups) == (4, 3)


class TestOneshotStats:
    """EvalStats.accumulate() one-shot score accumulation."""

    def test_accumulate_adds_score(self):
        stats = EvalStats()
        result = PuzzleResult(
            won=True, guess_count=1, mistake_count=0, invalid_count=0,
            solved_groups=["green", "yellow", "blue", "purple"], time_sec=5.0,
            total_tokens=500, score=5, groups_correct=4,
        )
        stats.accumulate(result)
        assert stats.total_score == 5

    def test_accumulate_classic_result_leaves_score_zero(self):
        """Classic-mode results default score=0, so total_score stays at 0."""
        stats = EvalStats()
        result = PuzzleResult(
            won=True, guess_count=4, mistake_count=0, invalid_count=0,
            solved_groups=["green", "yellow", "blue", "purple"], time_sec=10.0,
            total_tokens=1000,
        )
        stats.accumulate(result)
        assert stats.total_score == 0


class TestConnectionsGameMode:
    """ConnectionsGame(mode=...) selects the correct prompt template."""

    def test_oneshot_mode_loads_oneshot_template(self):
        game = ConnectionsGame(INPUTS, Path("logs"), mode="oneshot")
        assert game.mode == "oneshot"
        assert "<answer>" in game.prompt_template
        assert "<guess>" not in game.prompt_template

    def test_default_mode_loads_classic_template(self):
        game = ConnectionsGame(INPUTS, Path("logs"))
        assert game.mode == "classic"
        assert "<guess>" in game.prompt_template
        assert "<answer>" not in game.prompt_template


class TestOneshotEndToEnd:
    """run_evaluation drives the one-shot path end to end (mocked adapter)."""

    def test_perfect_submission_summary(self, tmp_path, make_game, patch_chat):
        """A perfect one-shot answer yields score 5 and a solved puzzle."""
        puzzle = make_puzzle()
        game = make_game(tmp_path, puzzle, mode="oneshot")
        answer = "<answer>\n" + "\n".join(
            ", ".join(g.words) for g in puzzle.groups
        ) + "\n</answer>"

        with patch_chat(make_response(answer)):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["mode"] == "oneshot"
        assert summary["puzzles_attempted"] == 1
        assert summary["puzzles_solved"] == 1
        # Puzzle has no trap annotations (trap_groups=None): base-only scoring,
        # perfect solve = 3 and per-puzzle max is 3.
        assert summary["total_score"] == 3
        assert summary["max_score"] == 3
        assert summary["avg_score"] == 3.0
        assert summary["total_guesses"] == 1
        assert summary["correct_guesses"] == 4
        assert summary["incorrect_guesses"] == 0
        assert summary["invalid_responses"] == 0

    def test_invalid_submission_summary(self, tmp_path, make_game, patch_chat):
        """An unparseable response scores 0 and counts as invalid."""
        puzzle = make_puzzle()
        game = make_game(tmp_path, puzzle, mode="oneshot")

        with patch_chat(make_response("I refuse to answer in the requested format.")):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["mode"] == "oneshot"
        assert summary["puzzles_solved"] == 0
        assert summary["total_score"] == 0
        assert summary["max_score"] == 3
        assert summary["avg_score"] == 0.0
        assert summary["invalid_responses"] == 1
        assert summary["incorrect_guesses"] == 0

    def test_trap_bonus_end_to_end(self, tmp_path, make_game, patch_chat):
        """Perfect answer + correct trap claim on an annotated puzzle scores 5/5."""
        puzzle = make_puzzle()
        # Cross-cutting 4-set: 2 Speed + 2 Smart words
        puzzle.trap_groups = [["FAST", "QUICK", "BRIGHT", "CLEVER"]]
        game = make_game(tmp_path, puzzle, mode="oneshot")
        answer = "<answer>\n" + "\n".join(
            ", ".join(g.words) for g in puzzle.groups
        ) + "\n</answer>\n<traps>\nFAST, QUICK, BRIGHT, CLEVER\n</traps>"

        with patch_chat(make_response(answer)):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["total_score"] == 5
        assert summary["max_score"] == 5
        assert summary["total_trap_bonus"] == 2
        assert summary["puzzles_solved"] == 1

    def test_false_trap_claim_voids_bonus(self, tmp_path, make_game, patch_chat):
        """A false first trap claim scores base only (only the first is judged)."""
        puzzle = make_puzzle()
        puzzle.trap_groups = [["FAST", "QUICK", "BRIGHT", "CLEVER"]]
        game = make_game(tmp_path, puzzle, mode="oneshot")
        answer = "<answer>\n" + "\n".join(
            ", ".join(g.words) for g in puzzle.groups
        ) + "\n</answer>\n<traps>\nAPPLE, BLUE, FAST, WISE\nFAST, QUICK, BRIGHT, CLEVER\n</traps>"

        with patch_chat(make_response(answer)):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["total_score"] == 3
        assert summary["max_score"] == 5
        assert summary["total_trap_bonus"] == 0

    def test_na_on_trapless_puzzle_earns_bonus(self, tmp_path, make_game, patch_chat):
        """Explicit N/A on a reviewed trap-free puzzle earns the +2."""
        puzzle = make_puzzle()
        puzzle.trap_groups = []  # reviewed, no traps
        game = make_game(tmp_path, puzzle, mode="oneshot")
        answer = "<answer>\n" + "\n".join(
            ", ".join(g.words) for g in puzzle.groups
        ) + "\n</answer>\n<traps>\nN/A\n</traps>"

        with patch_chat(make_response(answer)):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["total_score"] == 5
        assert summary["max_score"] == 5
        assert summary["total_trap_bonus"] == 2

    def test_api_error_still_counts_max_score(self, tmp_path, make_game, patch_chat):
        """An API-error puzzle contributes its per-puzzle max (annotated -> 5)
        so a partially-failed run's max_score stays honest."""
        puzzle = make_puzzle()
        puzzle.trap_groups = [["FAST", "QUICK", "RAPID", "SMART"]]
        game = make_game(tmp_path, puzzle, mode="oneshot")

        with patch_chat(RuntimeError("boom")):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["mode"] == "oneshot"
        assert summary["puzzles_attempted"] == 1
        assert summary["total_score"] == 0
        assert summary["max_score"] == 5
        assert summary["total_trap_bonus"] == 0


class TestOneshotTraps:
    """Trap claim parsing and scoring rules."""

    @pytest.fixture
    def trap_puzzle(self):
        """Puzzle with one 4-set trap and one 5-word superset trap, both
        cross-cutting (never 3+ words from one real group)."""
        return make_puzzle(trap_groups=[
            ["FAST", "QUICK", "BRIGHT", "CLEVER"],           # 2 Speed + 2 Smart
            ["RED", "YELLOW", "APPLE", "BANANA", "WISE"],    # 2/2/1 superset
        ])

    # --- parsing ---

    def test_parse_traps_block(self, mock_game):
        claims = mock_game._parse_oneshot_traps(
            "<answer>x</answer>\n<traps>\nFAST, QUICK, RAPID, SMART\napple, blue, red, wise\n</traps>")
        assert claims == [["FAST", "QUICK", "RAPID", "SMART"], ["APPLE", "BLUE", "RED", "WISE"]]

    def test_parse_traps_na(self, mock_game):
        assert mock_game._parse_oneshot_traps("<traps>\nN/A\n</traps>") == []
        assert mock_game._parse_oneshot_traps("<traps>none</traps>") == []
        assert mock_game._parse_oneshot_traps("<traps>NA.</traps>") == []

    def test_parse_traps_missing_block(self, mock_game):
        assert mock_game._parse_oneshot_traps("<answer>stuff</answer>") is None

    def test_parse_traps_ignores_decoy_in_thinking(self, mock_game):
        claims = mock_game._parse_oneshot_traps(
            "<thinking><traps>DECOY, DECOY, DECOY, DECOY</traps></thinking>\n<traps>N/A</traps>")
        assert claims == []

    # --- scoring ---

    @pytest.mark.parametrize("claims, expected", [
        pytest.param([["FAST", "QUICK", "BRIGHT", "CLEVER"]], 2,
                     id="correct_4set_claim"),
        pytest.param([["RED", "YELLOW", "APPLE", "WISE"]], 2,
                     # Any 4-subset of the 5-word superset that isn't the real group scores
                     id="subset_of_superset_claim"),
        pytest.param([["BRIGHT", "CLEVER", "SMART", "WISE"]], 0,
                     # A real group is never a trap, even when it shares a word with an
                     # annotation (WISE is in the superset). Rejected twice over: it is
                     # a real group, and it takes 4 words from one group. Note a real
                     # group can never be *inside* an annotated superset — that would
                     # need 3+ words from one group, which the scorer's <=2 rule bars
                     # (see test_three_from_one_group_never_scores_even_if_annotated).
                     id="exact_real_group_claim_rejected"),
        pytest.param([["FAST", "QUICK", "BRIGHT", "CLEVER"], ["APPLE", "BLUE", "FAST", "WISE"]], 2,
                     # Single-claim rule: a junk extra line no longer voids a correct first claim
                     id="only_first_claim_judged_bogus_second_ignored"),
        pytest.param([["APPLE", "BLUE", "FAST", "WISE"], ["FAST", "QUICK", "BRIGHT", "CLEVER"]], 0,
                     # ...and a correct second claim can't rescue a wrong first one
                     id="only_first_claim_judged_correct_second_ignored"),
        pytest.param([], 0, id="na_wrong_when_traps_exist"),
        pytest.param(None, 0, id="no_claim_no_bonus"),
        pytest.param([["FAST", "QUICK", "RAPID"]], 0, id="wrong_size_claim_voids"),
        pytest.param([["RED", "YELLOW", "APPLE", "BANANA", "WISE"]], 0,
                     # Claims must be EXACTLY 4 words — a full 5-word superset claim scores 0.
                     id="five_word_claim_rejected"),
        pytest.param([["FAST", "QUICK", "BRIGHT", "CLEVER", "CLEVER"]], 0,
                     # A 5-token claim with a duplicate collapses to a 4-set but must not
                     # pass the exactly-4-words gate.
                     id="duplicate_word_padding_rejected"),
    ])
    def test_score_trap_claims(self, mock_game, trap_puzzle, claims, expected):
        assert mock_game._score_trap_claims(trap_puzzle, claims) == expected

    def test_na_correct_on_trapless(self, mock_game):
        p = make_puzzle(id=1, date="", difficulty=1.0, trap_groups=[])
        assert mock_game._score_trap_claims(p, []) == 2

    def test_unreviewed_puzzle_inactive(self, mock_game):
        p = make_puzzle(id=1, date="", difficulty=1.0)  # trap_groups=None (default)
        assert mock_game._score_trap_claims(p, [["FAST", "QUICK", "BRIGHT", "CLEVER"]]) == 0

    def test_na_first_line_wins_despite_trailing_lines(self, mock_game):
        """N/A on the first line is the judged claim even with extra lines after
        (consistent with first-claim-only judging)."""
        p = make_puzzle(id=1, date="", difficulty=1.0, trap_groups=[])
        claims = mock_game._parse_oneshot_traps(
            "<traps>\nN/A\nFAST, QUICK, RAPID, SMART\n</traps>")
        assert claims == []
        assert mock_game._score_trap_claims(p, claims) == 2

    def test_real_yaml_246_traps(self):
        """Against the real YAML: 246's cross-cutting imitate trap scores;
        the removed 12-Monkeys overload (3 words from the movie group) doesn't."""
        game = ConnectionsGame(INPUTS, Path("logs"))
        p246 = {p.id: p for p in game.puzzles}[246]
        assert game._score_trap_claims(
            p246, [["ECHO", "MIME", "MONKEY", "PARROT"]]) == 2
        assert game._score_trap_claims(
            p246, [["APOLLO", "CANDLES", "FANTASTIC", "MONKEY"]]) == 0

    def test_three_from_one_group_never_scores_even_if_annotated(self, mock_game):
        """The no-3-from-one-category rule is enforced in the scorer, so a bad
        annotation (real group + one swap) still can't score."""
        p = make_puzzle(id=1, date="", difficulty=1.0,
                        trap_groups=[["FAST", "QUICK", "RAPID", "SMART"]])  # 3 Speed words
        assert mock_game._score_trap_claims(p, [["FAST", "QUICK", "RAPID", "SMART"]]) == 0

    def test_all_yaml_annotations_satisfy_no3_rule(self):
        """Every annotation must offer at least one scorable claim: a 4-subset
        that isn't a real group and takes <=2 words from any single group.
        4-set annotations must comply directly."""
        from itertools import combinations
        game = ConnectionsGame(INPUTS, Path("logs"))
        for p in game.puzzles:
            if not p.trap_groups:
                continue
            group_sets = [frozenset(w.upper() for w in g.words) for g in p.groups]
            for t in p.trap_groups:
                ws = frozenset(w.upper() for w in t)
                scorable = [
                    frozenset(c) for c in combinations(sorted(ws), 4)
                    if frozenset(c) not in group_sets
                    and all(len(frozenset(c) & g) <= 2 for g in group_sets)
                ]
                assert scorable, f"puzzle {p.id} trap {sorted(ws)} has no scorable claim"
                if len(ws) == 4:
                    assert frozenset(ws) in scorable, \
                        f"puzzle {p.id} 4-set trap {sorted(ws)} violates the no-3 rule"

    def test_real_yaml_839_corn_trap(self):
        game = ConnectionsGame(INPUTS, Path("logs"))
        p839 = {p.id: p for p in game.puzzles}[839]
        assert game._score_trap_claims(
            p839, [["SWEET", "KETTLE", "FRITTER", "POPPER"]]) == 2

    def test_canonical_yaml_traps_load(self):
        """The real YAML annotations load into Puzzle.trap_groups."""
        game = ConnectionsGame(INPUTS, Path("logs"))
        by_id = {p.id: p for p in game.puzzles}
        assert by_id[246].trap_groups is not None and len(by_id[246].trap_groups) == 2
        assert by_id[837].trap_groups == []  # reviewed, trap-free
        assert by_id[828].trap_groups == []
        # Superset annotation present (476 OVER-___ 5-set)
        assert any(len(t) == 5 for t in by_id[476].trap_groups)
        # Non-canonical puzzles are unreviewed
        assert all(p.trap_groups is None for p in game.puzzles if not p.canonical)


class TestOneshotFallbackPunctuation:
    """Tagless fallback parsing keeps hyphenated/apostrophe words intact."""

    def test_tagless_answer_with_traps_block(self, mock_game):
        """A tagless 4-line answer followed by a <traps> block must parse as
        exactly 4 groups — trap-claim lines look like answer lines and must
        not be scanned as extra groups (would force a structural invalid)."""
        response = (
            "APPLE, BANANA, CHERRY, GRAPE\n"
            "BLUE, GREEN, RED, YELLOW\n"
            "FAST, QUICK, RAPID, SWIFT\n"
            "BRIGHT, CLEVER, SMART, WISE\n"
            "<traps>\nFAST, QUICK, RAPID, SMART\n</traps>\n"
            "<confidence>0.9</confidence>"
        )
        groups = mock_game._parse_oneshot_response(response)
        assert len(groups) == 4
        assert groups[0] == ["APPLE", "BANANA", "CHERRY", "GRAPE"]

    def test_punctuated_canonical_words_survive_fallback(self, mock_game):
        """Canonical grids contain N.F.L., GREEK/ROMAN GOD, FOUR-LETTER WORDS —
        the tagless fallback must not reject lines containing them."""
        response = (
            "NASA, N.F.L., PARAMOUNT, SUBARU\n"
            "FICTIONAL BOXER, GREEK/ROMAN GOD, SPACECRAFT, THEATER\n"
            "EXPLETIVES, FOUR-LETTER WORDS, PROFANITY, SWEARING\n"
            "ABLE, CANE, EAVE, NOAA"
        )
        groups = mock_game._parse_oneshot_response(response)
        assert len(groups) == 4
        assert groups[0] == ["NASA", "N.F.L.", "PARAMOUNT", "SUBARU"]
        assert groups[1][1] == "GREEK/ROMAN GOD"

    def test_hyphenated_word_survives_fallback(self, mock_game):
        response = (
            "FLEUR-DE-LIS, BANANA, CHERRY, GRAPE\n"
            "ROCK 'N' ROLL, GREEN, RED, YELLOW\n"
            "FAST, QUICK, RAPID, SWIFT\n"
            "BRIGHT, CLEVER, SMART, WISE"
        )
        groups = mock_game._parse_oneshot_response(response)
        assert len(groups) == 4
        assert groups[0] == ["FLEUR-DE-LIS", "BANANA", "CHERRY", "GRAPE"]
        assert groups[1][0] == "ROCK 'N' ROLL"


class TestProviderPinning:
    """Test provider slug extraction."""

    @pytest.mark.parametrize("model_id, expected", [
        pytest.param("anthropic/claude-sonnet-4", "anthropic", id="anthropic"),
        pytest.param("openai/o3", "openai", id="openai"),
        pytest.param("google/gemini-2.5-pro", "google-ai-studio", id="google"),
        pytest.param("x-ai/grok-3", "xai", id="xai"),
        pytest.param(
            "anthropic/claude-sonnet-5", "amazon-bedrock",
            # TEMPORARY: claude-sonnet-5 400s on the Anthropic route (deprecated
            # top_p injected via reasoning.effort), so it's pinned to Bedrock
            # instead. Remove the override — and this case — once OpenRouter
            # fixes that route.
            id="sonnet_5_overridden_to_bedrock"),
        pytest.param(
            "anthropic/claude-sonnet-4.6", "anthropic",
            # The sonnet-5 override must not leak to sibling Anthropic models.
            id="sibling_anthropic_model_not_overridden_sonnet"),
        pytest.param(
            "anthropic/claude-opus-4.8", "anthropic",
            id="sibling_anthropic_model_not_overridden_opus"),
        pytest.param("deepseek/deepseek-r1-0528", None,
                     # DeepSeek models are hosted by third parties; pinning is skipped.
                     id="deepseek_skipped"),
        pytest.param("meta-llama/llama-3.3-70b-instruct", None,
                     # Meta-Llama models are hosted by third parties; pinning is skipped.
                     id="meta_llama_skipped"),
        pytest.param("qwen/qwen3-30b-a3b-instruct-2507", None,
                     # Qwen models are hosted by third parties; pinning is skipped.
                     id="qwen_skipped"),
        pytest.param("unknown/some-model", None, id="unknown_provider_returns_none"),
        pytest.param("", None, id="empty_string_returns_none"),
    ])
    def test_extract_provider_slug(self, model_id, expected):
        assert extract_provider_slug(model_id) == expected


class TestCacheInfo:
    """Test cache info extraction."""

    @pytest.mark.parametrize("response, cached_tokens, cache_discount", [
        pytest.param(
            {"usage": {"prompt_tokens_details": {"cached_tokens": 500},
                       "cache_discount": 0.5}},
            500, 0.5, id="present"),
        pytest.param(
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            None, None, id="absent"),
        pytest.param({}, None, None, id="empty"),
    ])
    def test_extract_cache_info(self, response, cached_tokens, cache_discount):
        info = extract_cache_info(response)
        assert info["cached_tokens"] == cached_tokens
        assert info["cache_discount"] == cache_discount


class TestChatProviderParam:
    """Test that chat() passes provider to payload."""

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_chat_without_provider(self, mock_key, mock_post):
        """Provider key should not appear when provider is None."""
        from connections_eval.adapters.openrouter_adapter import chat

        mock_post.return_value = _ok_response("hi", prompt_tokens=10, completion_tokens=5)

        chat([{"role": "user", "content": "test"}], "openai/o3", provider=None)

        payload = mock_post.call_args[1]["json"]
        assert "provider" not in payload

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_chat_with_provider(self, mock_key, mock_post):
        """Provider key should be set when provider is given."""
        from connections_eval.adapters.openrouter_adapter import chat

        mock_post.return_value = _ok_response("hi", prompt_tokens=10, completion_tokens=5)

        chat([{"role": "user", "content": "test"}], "openai/o3", provider="openai")

        payload = mock_post.call_args[1]["json"]
        assert payload["provider"] == {"order": ["openai"], "allow_fallbacks": False}

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_chat_without_session_id(self, mock_key, mock_post):
        """session_id key should not appear when session_id is None."""
        from connections_eval.adapters.openrouter_adapter import chat

        mock_post.return_value = _ok_response("hi", prompt_tokens=10, completion_tokens=5)

        chat([{"role": "user", "content": "test"}], "openrouter/fusion")

        payload = mock_post.call_args[1]["json"]
        assert "session_id" not in payload

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_chat_with_session_id(self, mock_key, mock_post):
        """session_id should be set top-level for sticky routing on cloaked models."""
        from connections_eval.adapters.openrouter_adapter import chat

        mock_post.return_value = _ok_response("hi", prompt_tokens=10, completion_tokens=5)

        chat([{"role": "user", "content": "test"}], "openrouter/fusion", session_id="T314:run1")

        payload = mock_post.call_args[1]["json"]
        assert payload["session_id"] == "T314:run1"
        # Cloaked model has no pinnable slug, so no provider order is forced.
        assert "provider" not in payload


class TestRankSessionIsolation:
    """session_id passed to the adapter must be unique per ranking attempt so
    repeated trials of one puzzle don't share a sticky-routing session."""

    def _build_game(self):
        game = ConnectionsGame(INPUTS, Path("logs"))
        game.run_id = "rank_test-model"
        game.logger = MagicMock()
        return game

    def _capture_session_ids(self, attempt):
        """Run one puzzle with a mocked adapter and return the session_ids the
        game passed to adapter.chat. The mock returns an unparseable guess so the
        game ends after MAX_INVALID turns regardless of which puzzle is chosen."""
        captured = []

        def fake_chat(messages, model_id, provider=None, session_id=None,
                      reasoning_effort=None, **kwargs):
            captured.append(session_id)
            return {
                "choices": [{"message": {"content": "no valid guess here"},
                             "finish_reason": "stop"}],
                "usage": {},
            }

        with patch("connections_eval.core.cl"), \
             patch("connections_eval.core.openrouter_adapter") as mock_adapter:
            mock_adapter.chat.side_effect = fake_chat
            mock_adapter.extract_provider_slug.return_value = None  # cloaked model
            game = self._build_game()
            puzzle = game.puzzles[0]
            model_name = next(iter(game.MODEL_CONFIG))
            game._run_puzzle_ai(puzzle, model_name, random.Random(0), attempt=attempt)
        return captured

    def test_normal_path_session_id_is_task_id(self):
        """attempt=None (normal eval) leaves session_id as the bare task_id."""
        sessions = self._capture_session_ids(attempt=None)
        assert sessions  # game actually called the adapter
        assert all(s.endswith(":rank_test-model") for s in sessions)
        assert all(":a" not in s for s in sessions)

    def test_rank_attempts_get_distinct_sessions(self):
        """Different attempts produce different session_ids; within an attempt
        every turn shares one session (so caching can still work per trial)."""
        a0 = self._capture_session_ids(attempt=0)
        a1 = self._capture_session_ids(attempt=1)
        assert len(set(a0)) == 1 and a0[0].endswith(":a0")
        assert len(set(a1)) == 1 and a1[0].endswith(":a1")
        assert a0[0] != a1[0]

    def test_rank_passes_incrementing_attempt_index(self):
        """_rank_puzzle hands _run_puzzle_ai a distinct attempt index per trial."""
        attempts = []

        def fake_run(puzzle, model_name, rng, attempt=None):
            attempts.append(attempt)
            return PuzzleResult(
                won=False, guess_count=0, mistake_count=0, invalid_count=0,
                solved_groups=[], time_sec=0.0, total_tokens=0,
            )

        game = self._build_game()
        puzzle = game.puzzles[0]
        with patch.object(game, "_run_puzzle_ai", side_effect=fake_run):
            game._rank_puzzle(puzzle, runs=3, model_name=next(iter(game.MODEL_CONFIG)))
        assert attempts == [0, 1, 2]

    def test_rank_run_id_is_timestamped(self):
        """A fresh rank invocation builds a timestamped run_id (not the static
        rank_{model} form) so session keys don't recur across invocations."""
        import re

        def fake_run(puzzle, model_name, rng, attempt=None):
            return PuzzleResult(
                won=False, guess_count=0, mistake_count=0, invalid_count=0,
                solved_groups=[], time_sec=0.0, total_tokens=0,
            )

        # Fresh game: logger is None so the rank entrypoint assigns run_id.
        game = ConnectionsGame(INPUTS, Path("logs"))
        puzzle_id = game.puzzles[0].id
        model_name = next(iter(game.MODEL_CONFIG))
        with patch("connections_eval.core.setup_logger", return_value=MagicMock()), \
             patch.object(game, "_run_puzzle_ai", side_effect=fake_run):
            game.rank_puzzle(puzzle_id, runs=1, model_name=model_name)
        assert game.run_id != f"rank_{model_name}"
        assert re.fullmatch(
            rf"rank_\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}-\d{{2}}-\d{{2}}_{re.escape(model_name)}",
            game.run_id,
        )


def test_token_counting():
    """Test token counting utilities."""
    from connections_eval.utils.tokens import count_tokens, extract_token_usage

    # Basic token counting
    count = count_tokens("Hello world")
    assert count > 0

    # Token usage extraction with API data
    response_data = {
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5
        }
    }
    prompt_tokens, completion_tokens, method = extract_token_usage(response_data)
    assert prompt_tokens == 10
    assert completion_tokens == 5
    assert method == "API"

    # Token usage extraction without API data
    response_data = {}
    prompt_tokens, completion_tokens, method = extract_token_usage(response_data)
    assert prompt_tokens is None
    assert completion_tokens is None
    assert method == "APPROXIMATE"


class TestBackoffAccumulator:
    """Retry backoff is attributed via a thread-local so callers can split
    inference time from time spent waiting in retry sleeps."""

    def test_accumulator_sums_sleeps_and_resets_per_call(self, monkeypatch):
        import requests
        from connections_eval.utils import retry as retry_mod

        sleeps = []
        monkeypatch.setattr(retry_mod.time, "sleep", lambda s: sleeps.append(s))
        # Make jitter deterministic so we can assert the exact accumulated value.
        monkeypatch.setattr(retry_mod.random, "uniform", lambda a, b: 0.0)

        attempts = {"n": 0}

        @retry_mod.retry_with_backoff(
            max_retries=3, base_delay=1.0, exceptions=(requests.RequestException,)
        )
        def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise requests.RequestException("boom")
            return "ok"

        assert flaky() == "ok"
        # base_delay * 2^0 + base_delay * 2^1 = 1 + 2 = 3s
        assert retry_mod.get_last_backoff_sec() == pytest.approx(3.0)
        assert sleeps == [1.0, 2.0]

        # Second call must start fresh
        attempts["n"] = 0
        sleeps.clear()
        flaky()
        assert retry_mod.get_last_backoff_sec() == pytest.approx(3.0)

    def test_accumulator_zero_on_first_attempt_success(self, monkeypatch):
        import requests
        from connections_eval.utils import retry as retry_mod

        monkeypatch.setattr(retry_mod.time, "sleep", lambda s: None)

        @retry_mod.retry_with_backoff(
            max_retries=3, base_delay=1.0, exceptions=(requests.RequestException,)
        )
        def clean():
            return "ok"

        clean()
        assert retry_mod.get_last_backoff_sec() == 0.0


class TestInsufficientCreditsAbort:
    """402 aborts the run immediately: no retries, no partial summary."""

    def test_retry_decorator_does_not_retry_non_retryable(self):
        from connections_eval.utils.retry import retry_with_backoff
        from connections_eval.adapters.openrouter_adapter import InsufficientCreditsError
        calls = []

        @retry_with_backoff(max_retries=5, base_delay=0.01)
        def boom():
            calls.append(1)
            raise InsufficientCreditsError("no credits")

        with pytest.raises(InsufficientCreditsError):
            boom()
        assert len(calls) == 1  # exactly one attempt, no retries

    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    def test_chat_raises_credits_error_on_402(self, mock_post, mock_key):
        from connections_eval.adapters.openrouter_adapter import chat, InsufficientCreditsError
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 402
        resp.json.return_value = {"error": {"message": "can only afford 100 tokens"}}
        mock_post.return_value = resp
        with pytest.raises(InsufficientCreditsError, match="can only afford"):
            chat([{"role": "user", "content": "x"}], "openai/o3")
        assert mock_post.call_count == 1  # no retry loop

    def test_run_evaluation_aborts_without_summary(self, tmp_path, make_game, patch_chat):
        """A credit wall mid-run raises out of run_evaluation (no summary,
        so nothing partial lands on the leaderboard)."""
        from connections_eval.adapters.openrouter_adapter import InsufficientCreditsError
        puzzle = make_puzzle(trap_groups=[])
        game = make_game(tmp_path, puzzle, mode="oneshot")
        with patch_chat(InsufficientCreditsError("no credits")):
            with pytest.raises(InsufficientCreditsError):
                game.run_evaluation("test-model", puzzle_ids=[477])


class TestModelPreflight:
    """assert_model_exists fails fast on bad slugs, skips on catalog errors."""

    def _reset_cache(self):
        import connections_eval.adapters.openrouter_adapter as oa
        oa._MODEL_CATALOG = None

    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    @patch("connections_eval.adapters.openrouter_adapter.requests.get")
    def test_known_model_passes(self, mock_get, mock_key):
        from connections_eval.adapters.openrouter_adapter import assert_model_exists
        self._reset_cache()
        mock_get.return_value = MagicMock(
            ok=True, **{"json.return_value": {"data": [{"id": "openai/o3"}]},
                        "raise_for_status.return_value": None})
        assert_model_exists("openai/o3")  # no raise

    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    @patch("connections_eval.adapters.openrouter_adapter.requests.get")
    def test_free_variant_matches_base(self, mock_get, mock_key):
        from connections_eval.adapters.openrouter_adapter import assert_model_exists
        self._reset_cache()
        mock_get.return_value = MagicMock(
            ok=True, **{"json.return_value": {"data": [{"id": "poolside/laguna-m.1"}]},
                        "raise_for_status.return_value": None})
        assert_model_exists("poolside/laguna-m.1:free")  # base id match, no raise

    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    @patch("connections_eval.adapters.openrouter_adapter.requests.get")
    def test_unknown_model_raises(self, mock_get, mock_key):
        from connections_eval.adapters.openrouter_adapter import assert_model_exists
        self._reset_cache()
        mock_get.return_value = MagicMock(
            ok=True, **{"json.return_value": {"data": [{"id": "openai/o3"}]},
                        "raise_for_status.return_value": None})
        with pytest.raises(ValueError, match="not found in OpenRouter"):
            assert_model_exists("openai/gpt-99-typo")
        self._reset_cache()

    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    @patch("connections_eval.adapters.openrouter_adapter.requests.get",
           side_effect=Exception("network down"))
    def test_catalog_fetch_failure_skips_check(self, mock_get, mock_key):
        from connections_eval.adapters.openrouter_adapter import assert_model_exists
        self._reset_cache()
        assert_model_exists("anything/at-all")  # warns, does not raise
        self._reset_cache()


class TestReasoningEffort:
    """reasoning_effort plumbs through to the OpenRouter request payload."""

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def _call_chat(self, mock_key, mock_post, model, **kwargs):
        from connections_eval.adapters.openrouter_adapter import chat

        mock_post.return_value = _ok_response("hi", prompt_tokens=10, completion_tokens=5)

        chat([{"role": "user", "content": "test"}], model, **kwargs)
        return mock_post.call_args.kwargs["json"]

    def test_thinking_model_defaults_to_minimal(self):
        payload = self._call_chat(model="openai/o3", thinking=True)
        assert payload["reasoning"] == {"effort": "minimal"}

    def test_thinking_model_effort_override(self):
        payload = self._call_chat(model="openai/o3", reasoning_effort="high", thinking=True)
        assert payload["reasoning"] == {"effort": "high"}

    def test_non_thinking_model_ignores_effort(self):
        payload = self._call_chat(model="not-a-real/thinking-model", reasoning_effort="high")
        assert "reasoning" not in payload


class TestAdapterChoicesFix:
    """OpenRouter sometimes returns HTTP 200 with an error body (no `choices`).
    The adapter must surface that as a RequestException so retry engages
    instead of letting a KeyError escape."""

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_200_with_no_choices_is_retried(self, mock_key, mock_post, monkeypatch):
        from connections_eval.adapters.openrouter_adapter import chat
        from connections_eval.utils import retry as retry_mod

        monkeypatch.setattr(retry_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(retry_mod.random, "uniform", lambda a, b: 0.0)

        bad_response = MagicMock()
        bad_response.ok = True
        bad_response.json.return_value = {"error": {"message": "upstream throttled"}}
        bad_response.raise_for_status.return_value = None

        good_response = _ok_response("hi", prompt_tokens=10, completion_tokens=5)

        mock_post.side_effect = [bad_response, good_response]

        result = chat([{"role": "user", "content": "test"}], "openai/o3", provider=None)

        # Both calls happened — malformed 200 was retried, not escaped as KeyError.
        assert mock_post.call_count == 2
        assert result["choices"][0]["message"]["content"] == "hi"
        # The one retry sleep should be attributed as backoff on the response.
        assert result["_backoff_sec"] > 0

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_successful_first_call_stashes_zero_backoff(self, mock_key, mock_post):
        from connections_eval.adapters.openrouter_adapter import chat

        mock_post.return_value = _ok_response("hi", prompt_tokens=10, completion_tokens=5)

        result = chat([{"role": "user", "content": "test"}], "openai/o3", provider=None)

        assert result["_backoff_sec"] == 0.0


class TestAdapterPartialResponse:
    """OpenRouter occasionally returns HTTP 200 with `choices` present, a
    partial (or empty) `content`, and a `usage` block whose completion_tokens
    is 0 — a transient upstream fault, not a real model answer. That must be
    retried (via PartialResponseError), but a genuine max_tokens truncation
    (finish_reason "length" with nonzero completion_tokens) must not be."""

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_zero_usage_partial_content_is_retried(self, mock_key, mock_post, monkeypatch):
        from connections_eval.adapters.openrouter_adapter import chat
        from connections_eval.utils import retry as retry_mod

        monkeypatch.setattr(retry_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(retry_mod.random, "uniform", lambda a, b: 0.0)

        partial_response = _ok_response(
            "<answer>\nAPPLE, BANANA, CHERRY, GR",
            prompt_tokens=0, completion_tokens=0,
            native_finish_reason="stop", provider="SomeProvider")

        good_response = _ok_response("hi", prompt_tokens=10, completion_tokens=5)

        mock_post.side_effect = [partial_response, good_response]

        result = chat([{"role": "user", "content": "test"}], "openai/o3", provider=None)

        # Both calls happened — the zero-usage partial was retried, not returned.
        assert mock_post.call_count == 2
        assert result["choices"][0]["message"]["content"] == "hi"

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_length_finish_reason_with_tokens_is_not_retried(self, mock_key, mock_post):
        """A genuine max_tokens truncation (nonzero completion_tokens) must be
        returned as-is, not treated as a transient partial-response fault."""
        from connections_eval.adapters.openrouter_adapter import chat

        mock_post.return_value = _ok_response(
            "<answer>\nAPPLE, BANANA, CHERRY, GR",
            prompt_tokens=100, completion_tokens=25000, finish_reason="length")

        result = chat([{"role": "user", "content": "test"}], "openai/o3", provider=None)

        assert mock_post.call_count == 1
        assert result["choices"][0]["message"]["content"].startswith("<answer>")


# --- shared oneshot/classic exchange scaffolding, used by both
# TestSharedExchangeScaffolding and TestOneshotLintRepairLoop below: they
# exercise the same _run_exchange plumbing in core.py. ----------------------

# One annotated trap set: one word from each real group, so it satisfies the
# cross-cutting rule in _score_trap_claims.
_TRAP = ["APPLE", "BLUE", "FAST", "BRIGHT"]
_TRAP_GROUPS = [_TRAP]
# Distinguishes "caller didn't say" from trap_groups=None (unreviewed puzzle)
# and trap_groups=[] (reviewed, trap-free), which score differently.
_DEFAULT_TRAPS = object()


def _oneshot_answer(traps=None):
    answer = "<answer>\n" + "\n".join(
        ", ".join(g.words) for g in make_test_groups()) + "\n</answer>"
    return answer if traps is None else f"{answer}\n<traps>\n{traps}\n</traps>"


def _classic_guesses(**response_kwargs):
    return [make_response(f"<guess>{', '.join(g.words)}</guess>", **response_kwargs)
            for g in make_test_groups()]


@pytest.fixture
def run_exchange(make_game, patch_chat, tmp_path):
    """Run one oneshot/classic puzzle exchange end to end (mocked adapter,
    mocked controllog), capturing every logged/emitted side effect."""
    def _run(mode, side_effect, trap_groups=_DEFAULT_TRAPS, game=None):
        if trap_groups is _DEFAULT_TRAPS:
            trap_groups = _TRAP_GROUPS
        puzzle = make_puzzle(trap_groups=trap_groups)
        game = game or make_game(tmp_path, puzzle, mode=mode,
                                 mappings={"test-model": "openai/o3"},
                                 reasoning_effort="high")

        cap = SimpleNamespace(exchanges=[], events=[], moves=[], prompts=[],
                              completions=[], summary=None, chat=None)
        with patch_chat(side_effect) as chat_mock, \
             patch("connections_eval.core.log_exchange",
                   side_effect=lambda logger, data: cap.exchanges.append(data)), \
             patch.object(core_mod.cl, "event",
                          side_effect=lambda **kw: cap.events.append(kw)), \
             patch.object(core_mod.cl, "state_move",
                          side_effect=lambda **kw: cap.moves.append(kw)), \
             patch.object(core_mod.cl, "model_prompt",
                          side_effect=lambda **kw: cap.prompts.append(kw)), \
             patch.object(core_mod.cl, "model_completion",
                          side_effect=lambda **kw: cap.completions.append(kw)):
            cap.summary = game.run_evaluation("test-model", puzzle_ids=[477])
        cap.chat = chat_mock
        return cap
    return _run


class TestSharedExchangeScaffolding:
    """Both runners share _run_exchange for transport, accounting and telemetry.
    These lock in the compatibility contract that MotherDuck aggregation
    (scripts/extract_summaries.py, scripts/generate_logs_view.py) parses, plus the
    mode-specific pieces that must stay distinct."""

    # --- shared plumbing -------------------------------------------------

    def test_reasoning_effort_reaches_adapter_in_both_modes(self, run_exchange):
        for mode, content in (("oneshot", _oneshot_answer()),
                              ("classic", "<guess>APPLE, BANANA, CHERRY, GRAPE</guess>")):
            cap = run_exchange(mode, [make_response(content)] * 6)
            assert cap.chat.call_args.kwargs["reasoning_effort"] == "high"

    def test_totals_accumulate_across_classic_turns(self, run_exchange):
        """Four turns of 100/50 tokens, $0.01 and 40 cached tokens each."""
        cap = run_exchange("classic", _classic_guesses(cost=0.01, cached=40))

        assert cap.summary["total_prompt_tokens"] == 400
        assert cap.summary["total_completion_tokens"] == 200
        assert cap.summary["total_tokens"] == 600
        assert cap.summary["total_cached_tokens"] == 160
        assert cap.summary["total_cost"] == pytest.approx(0.04)
        assert cap.summary["total_upstream_cost"] == pytest.approx(0.08)
        assert cap.summary["token_count_method"] == "API"

    def test_oneshot_totals_come_from_single_call(self, run_exchange):
        cap = run_exchange("oneshot",
                           [make_response(_oneshot_answer(), cost=0.01, cached=40)])

        assert cap.summary["total_prompt_tokens"] == 100
        assert cap.summary["total_completion_tokens"] == 50
        assert cap.summary["total_cached_tokens"] == 40
        assert cap.summary["total_cost"] == pytest.approx(0.01)
        assert cap.summary["total_upstream_cost"] == pytest.approx(0.02)

    # --- exchange log shape (parsed by generate_logs_view) ---------------

    _BASE_LOG_FIELDS = [
        "run_id", "model", "puzzle_id", "guess_index", "request", "response",
        "thinking", "guess", "confidence", "latency_ms", "backoff_ms",
        "inference_ms", "prompt_tokens", "completion_tokens", "result",
    ]
    # Response metadata appended unconditionally, after any cost/cache fields.
    _RESPONSE_META_FIELDS = [
        "finish_reason", "native_finish_reason", "provider", "usage",
    ]

    def test_classic_exchange_log_field_order(self, run_exchange):
        cap = run_exchange("classic", _classic_guesses(cost=0.01, cached=40))

        assert list(cap.exchanges[0].keys()) == self._BASE_LOG_FIELDS + [
            "cost", "upstream_cost", "cached_tokens"] + self._RESPONSE_META_FIELDS
        # Classic exchanges carry no one-shot score fields
        assert all("score" not in e for e in cap.exchanges)

    def test_oneshot_exchange_log_field_order(self, run_exchange):
        cap = run_exchange("oneshot",
                           [make_response(_oneshot_answer(), cost=0.01, cached=40)])

        assert list(cap.exchanges[0].keys()) == self._BASE_LOG_FIELDS + [
            "score", "groups_correct", "trap_bonus", "trap_claims", "lint_retries",
            "cost", "upstream_cost", "cached_tokens"] + self._RESPONSE_META_FIELDS

    def test_classic_exchange_log_records_post_guess_index(self, run_exchange):
        """Classic logs the guess index *after* the guess is counted, so a run of
        correct guesses logs 1..4 rather than 0..3."""
        cap = run_exchange("classic", _classic_guesses())

        assert [e["guess_index"] for e in cap.exchanges] == [1, 2, 3, 4]
        assert [c["payload"]["guess_index"] for c in cap.completions] == [1, 2, 3, 4]

    def test_oneshot_exchange_log_carries_score_fields(self, run_exchange):
        cap = run_exchange("oneshot",
                           [make_response(_oneshot_answer(traps=", ".join(_TRAP)))])

        logged = cap.exchanges[0]
        assert logged["guess_index"] == 0
        assert logged["result"] == "ONESHOT_SCORE_5_GROUPS_4_TRAP_2_MAX_5"
        assert logged["score"] == 5
        assert logged["groups_correct"] == 4
        assert logged["trap_bonus"] == 2
        assert logged["trap_claims"] == ["APPLE, BLUE, FAST, BRIGHT"]
        # 'guess' is the <answer> block, not the classic <guess> tag
        assert "APPLE, BANANA, CHERRY, GRAPE" in logged["guess"]

    def test_exchange_log_carries_response_metadata(self, run_exchange):
        """finish_reason/native_finish_reason/provider/usage
        must reach the JSONL exchange log so a transient provider fault (see
        PartialResponseError) can be told apart from a bad model answer."""
        response = make_response(_oneshot_answer(),
                                 native_finish_reason="STOP", provider="Mercury")

        cap = run_exchange("oneshot", [response])

        logged = cap.exchanges[0]
        assert logged["finish_reason"] == "stop"
        assert logged["native_finish_reason"] == "STOP"
        assert logged["provider"] == "Mercury"
        assert logged["usage"] == {"prompt_tokens": 100, "completion_tokens": 50}

    # --- controllog telemetry (parsed by extract_summaries) --------------

    def test_classic_telemetry_payloads(self, run_exchange):
        cap = run_exchange("classic", _classic_guesses())

        assert [p["payload"] for p in cap.prompts] == [
            {"puzzle_id": 477, "guess_index": i} for i in (1, 2, 3, 4)]
        # Verdict strings feed the classic solve/mistake aggregation
        assert [c["payload"] for c in cap.completions] == [
            {"puzzle_id": 477, "guess_index": 1, "result": "CORRECT. NEXT GUESS?"},
            {"puzzle_id": 477, "guess_index": 2, "result": "CORRECT. NEXT GUESS?"},
            {"puzzle_id": 477, "guess_index": 3, "result": "CORRECT. NEXT GUESS?"},
            {"puzzle_id": 477, "guess_index": 4, "result": "CORRECT"},
        ]

    def test_oneshot_telemetry_payload_carries_score_fields(self, run_exchange):
        cap = run_exchange("oneshot",
                           [make_response(_oneshot_answer(traps=", ".join(_TRAP)))])

        assert cap.prompts[0]["payload"] == {"puzzle_id": 477, "guess_index": 0}
        assert cap.completions[0]["payload"] == {
            "puzzle_id": 477, "guess_index": 0,
            "result": "ONESHOT_SCORE_5_GROUPS_4_TRAP_2_MAX_5",
            "score": 5, "groups_correct": 4, "trap_bonus": 2,
        }

    # --- state transitions ----------------------------------------------

    @staticmethod
    def _transitions(cap):
        return [(m["from_"], m["to"]) for m in cap.moves]

    def test_solved_puzzle_moves_new_wip_done(self, run_exchange):
        for mode, side_effect in (("classic", _classic_guesses()),
                                  ("oneshot", [make_response(_oneshot_answer())])):
            cap = run_exchange(mode, side_effect)
            assert self._transitions(cap) == [("NEW", "WIP"), ("WIP", "DONE")]

    def test_api_error_moves_wip_failed_exactly_once(self, run_exchange):
        for mode in ("classic", "oneshot"):
            cap = run_exchange(mode, RuntimeError("boom"))
            assert self._transitions(cap) == [("NEW", "WIP"), ("WIP", "FAILED")]
            assert cap.moves[-1]["payload"] == {"puzzle_id": 477, "reason": "api_error"}

    def test_prompt_build_failure_leaves_no_wip_task(self, make_game, run_exchange, tmp_path):
        """WIP is only claimed once the prompt exists, so a prompt-build failure
        can't strand a task in WIP with no terminal transition."""
        for mode in ("classic", "oneshot"):
            game = make_game(tmp_path, make_puzzle(trap_groups=_TRAP_GROUPS), mode=mode,
                             mappings={"test-model": "openai/o3"}, reasoning_effort="high")
            with patch.object(game, "_build_initial_messages",
                              side_effect=RuntimeError("bad template")):
                cap = run_exchange(mode, [make_response("")], game=game)
            assert cap.moves == []
            assert cap.summary["puzzles_attempted"] == 0

    # --- error paths -----------------------------------------------------

    def test_classic_api_error_event_has_no_oneshot_marker(self, run_exchange):
        """The ONESHOT_API_ERROR_MAX_n mode marker must not leak into classic
        runs — MotherDuck uses it to classify a fully-failed run's mode."""
        cap = run_exchange("classic", RuntimeError("boom"))

        assert [e["result"] for e in cap.exchanges] == ["API_ERROR"]
        errors = [e for e in cap.events if e["kind"] == "model_response_error"]
        assert len(errors) == 1
        assert "result" not in errors[0]["payload"]

    def test_oneshot_api_error_event_carries_max_marker(self, run_exchange):
        cap = run_exchange("oneshot", RuntimeError("boom"))

        assert [e["result"] for e in cap.exchanges] == ["API_ERROR"]
        errors = [e for e in cap.events if e["kind"] == "model_response_error"]
        assert errors[0]["payload"]["result"] == "ONESHOT_API_ERROR_MAX_5"
        # A failed call claims no tokens or cost, but still contributes the ceiling
        assert cap.summary["total_tokens"] == 0
        assert cap.summary["total_cost"] == 0.0
        assert cap.summary["max_score"] == 5

    def test_insufficient_credits_aborts_both_modes(self, run_exchange):
        """402 must abort the run with no summary, in both modes."""
        for mode in ("classic", "oneshot"):
            with pytest.raises(openrouter_adapter.InsufficientCreditsError):
                run_exchange(mode, openrouter_adapter.InsufficientCreditsError("no credits"))

    def test_insufficient_credits_aborts_parallel_run(self, make_game, patch_chat, tmp_path):
        puzzle = make_puzzle(trap_groups=_TRAP_GROUPS)
        game = make_game(tmp_path, puzzle, mode="oneshot",
                         mappings={"test-model": "openai/o3"}, reasoning_effort="high")
        with patch_chat(openrouter_adapter.InsufficientCreditsError("no credits")), \
             pytest.raises(openrouter_adapter.InsufficientCreditsError):
            game.run_evaluation("test-model", puzzle_ids=[477], threads=4)

    # --- per-puzzle MAX ceiling (parsed out of the result strings) --------

    def test_unreviewed_puzzle_caps_at_max_3(self, run_exchange):
        """trap_groups=None means traps were never reviewed: no bonus is possible
        even for a correct-looking claim, and every marker carries MAX_3."""
        cap = run_exchange("oneshot",
                           [make_response(_oneshot_answer(traps=", ".join(_TRAP)))],
                           trap_groups=None)

        assert cap.exchanges[0]["result"] == "ONESHOT_SCORE_3_GROUPS_4_TRAP_0_MAX_3"
        assert cap.exchanges[0]["trap_bonus"] == 0
        assert cap.summary["total_score"] == 3
        assert cap.summary["max_score"] == 3

    def test_reviewed_trapless_puzzle_scores_na_claim(self, run_exchange):
        """trap_groups=[] means reviewed and trap-free, so N/A earns the +2."""
        cap = run_exchange("oneshot",
                           [make_response(_oneshot_answer(traps="N/A"))], trap_groups=[])

        assert cap.exchanges[0]["result"] == "ONESHOT_SCORE_5_GROUPS_4_TRAP_2_MAX_5"
        assert cap.exchanges[0]["trap_claims"] == ["N/A"]
        assert cap.summary["max_score"] == 5

    def test_invalid_and_error_markers_carry_unreviewed_ceiling(self, run_exchange):
        # A response with no <answer> block now buys MAX_LINT_RETRIES repair
        # turns first; only the last exchange carries the scoring verdict.
        invalid = run_exchange("oneshot",
                               [make_response("no answer here")] * 3, trap_groups=None)
        assert invalid.exchanges[-1]["result"] == "ONESHOT_INVALID_MAX_3"
        assert invalid.exchanges[-1]["trap_claims"] == []
        assert invalid.summary["invalid_responses"] == 1

        errored = run_exchange("oneshot", RuntimeError("boom"), trap_groups=None)
        errors = [e for e in errored.events if e["kind"] == "model_response_error"]
        assert errors[0]["payload"]["result"] == "ONESHOT_API_ERROR_MAX_3"
        assert errored.summary["max_score"] == 3

    # --- backoff accounting ----------------------------------------------

    def test_backoff_is_split_out_of_inference_time(self, run_exchange):
        """Retry sleep is attributed as backoff, kept out of inference_ms, and
        posted as its own model_backoff event."""
        # Each call burns 60ms of wall time and reports 10ms of it as backoff, so
        # inference_ms lands strictly between 0 and latency_ms — the subtraction
        # is only observable when latency exceeds the backoff.
        responses = _classic_guesses()
        for r in responses:
            r["_backoff_sec"] = 0.01

        def slow_chat(*args, **kwargs):
            time.sleep(0.06)
            return responses.pop(0)

        cap = run_exchange("classic", slow_chat)

        assert cap.summary["total_backoff_sec"] == pytest.approx(0.04)
        assert all(e["backoff_ms"] == 10 for e in cap.exchanges)
        assert all(e["latency_ms"] >= 50 for e in cap.exchanges)
        assert all(e["inference_ms"] == e["latency_ms"] - 10 for e in cap.exchanges)
        assert all(0 < e["inference_ms"] < e["latency_ms"] for e in cap.exchanges)
        # Run-level inference time is wall time minus the backoff share
        assert cap.summary["total_inference_sec"] == pytest.approx(
            cap.summary["total_time_sec"] - 0.04, abs=1e-3)

        backoffs = [e for e in cap.events if e["kind"] == "model_backoff"]
        assert len(backoffs) == 4
        assert backoffs[0]["payload"]["backoff_ms"] == 10

    def test_error_path_attributes_last_backoff(self, run_exchange):
        """Backoff burned before a call finally failed is still accounted for."""
        for mode in ("classic", "oneshot"):
            with patch.object(core_mod, "get_last_backoff_sec", return_value=2.0):
                cap = run_exchange(mode, RuntimeError("boom"))
            assert cap.summary["total_backoff_sec"] == pytest.approx(2.0)

    # --- thread isolation -------------------------------------------------

    def test_parallel_puzzles_get_isolated_contexts_and_summed_totals(
            self, make_game, patch_chat, tmp_path):
        """Two puzzles across two threads: each gets its own task/session id and
        its own accounting, and the run totals are the sum."""
        # Puzzle 246 swaps GRAPE -> MELON so the two prompts are distinguishable
        # and each thread's request can be tied back to its own puzzle.
        puzzles = [make_puzzle(trap_groups=_TRAP_GROUPS), make_puzzle(trap_groups=_TRAP_GROUPS)]
        puzzles[1].id = 246
        puzzles[1].words = ["MELON" if w == "GRAPE" else w for w in puzzles[1].words]
        puzzles[1].groups = make_test_groups()
        puzzles[1].groups[0].words = ["APPLE", "BANANA", "CHERRY", "MELON"]
        answers = {
            477: _oneshot_answer(),
            246: "<answer>\n" + "\n".join(
                ", ".join(g.words) for g in puzzles[1].groups) + "\n</answer>",
        }
        game = make_game(tmp_path, puzzles=puzzles, mode="oneshot",
                         mappings={"test-model": "openai/o3"})

        # Correlating each request's session_id with the puzzle its prompt is for
        # catches a context swap between threads — which asserting on id sets
        # alone would not.
        sessions = {}

        def capture_chat(messages, model_id, provider=None, session_id=None, **kwargs):
            rendered = " ".join(m["content"] for m in messages)
            pid = 246 if "MELON" in rendered else 477
            sessions[pid] = session_id
            return make_response(answers[pid])

        prompts, completions, exchanges = [], [], []
        with patch_chat(capture_chat), \
             patch("connections_eval.core.log_exchange",
                   side_effect=lambda logger, data: exchanges.append(data)), \
             patch.object(core_mod.cl, "model_prompt",
                          side_effect=lambda **kw: prompts.append(kw)), \
             patch.object(core_mod.cl, "model_completion",
                          side_effect=lambda **kw: completions.append(kw)):
            summary = game.run_evaluation("test-model", puzzle_ids=[477, 246], threads=2)

        assert summary["puzzles_attempted"] == 2
        assert summary["puzzles_solved"] == 2
        assert summary["total_prompt_tokens"] == 200
        assert summary["total_completion_tokens"] == 100

        # Each puzzle's sticky-routing session and telemetry task id are its own
        expected = {pid: f"T{pid}:{game.run_id}" for pid in (477, 246)}
        assert sessions == expected
        assert {p["payload"]["puzzle_id"]: p["task_id"] for p in prompts} == expected
        assert {c["payload"]["puzzle_id"]: c["task_id"] for c in completions} == expected
        assert {e["puzzle_id"] for e in exchanges} == {477, 246}
        # Per-puzzle accounting stayed separate rather than doubling up
        assert all(e["prompt_tokens"] == 100 for e in exchanges)


class TestOneshotLintRepairLoop:
    """A structurally broken one-shot response buys up to MAX_LINT_RETRIES repair
    turns before it is scored. The repair turns must stay invisible to the
    MotherDuck aggregation, which keys off the ONESHOT / INVALID prefixes.

    Reuses the run_exchange fixture: it exercises the same _run_exchange
    plumbing as TestSharedExchangeScaffolding above."""

    # Real granite-4.2-8b failure shape: one line space-separated, rest fine.
    _BROKEN_ANSWER = (
        "<answer>\n"
        "APPLE BANANA CHERRY GRAPE\n"
        "BLUE, GREEN, RED, YELLOW\n"
        "FAST, QUICK, RAPID, SWIFT\n"
        "BRIGHT, CLEVER, SMART, WISE\n"
        "</answer>\n"
        "<traps>\nAPPLE, BLUE, FAST, BRIGHT\n</traps>"
    )
    _ANSWER_ONLY = _oneshot_answer()
    _ANSWER_OPEN_ONLY = _ANSWER_ONLY.removesuffix("</answer>")

    def _run(self, run_exchange, side_effect, **kwargs):
        return run_exchange("oneshot", side_effect, **kwargs)

    def test_answer_resubmission_is_merged_and_scored(self, run_exchange):
        """The model re-sends only the <answer> block; it is spliced back into the
        original response, so the untouched <traps> claim still earns its bonus."""
        cap = self._run(run_exchange, [make_response(self._BROKEN_ANSWER),
                                       make_response(self._ANSWER_ONLY)])

        assert [e["result"] for e in cap.exchanges] == [
            "LINT_RETRY_answer.words_per_line",
            "ONESHOT_SCORE_5_GROUPS_4_TRAP_2_MAX_5",
        ]
        assert cap.summary["puzzles_solved"] == 1
        assert cap.summary["total_score"] == 5
        assert cap.summary["invalid_responses"] == 0

    def test_repair_request_names_the_rule_and_the_segment(self, run_exchange):
        cap = self._run(run_exchange, [make_response(self._BROKEN_ANSWER),
                                       make_response(self._ANSWER_ONLY)])

        # The adapter is handed the same list object every turn, so read the
        # final transcript rather than a per-call snapshot.
        transcript = cap.chat.call_args_list[-1].args[0]
        assert [m["role"] for m in transcript] == ["system", "user", "assistant", "user"]
        followup = transcript[-1]["content"]
        assert followup.startswith("Response failed linting rule answer.words_per_line: ")
        assert "re-submit only the failed segment answer" in followup

    def test_unclosed_answer_repair_is_completed_and_scored(self, run_exchange):
        cap = self._run(run_exchange, [make_response(self._BROKEN_ANSWER),
                                       make_response(self._ANSWER_OPEN_ONLY)])

        assert cap.chat.call_count == 2
        assert cap.summary["puzzles_solved"] == 1
        assert cap.summary["total_score"] == 5
        assert cap.summary["lint_retries"] == 1

    def test_repair_prompt_omits_long_thinking_context(self, run_exchange):
        initial = "<thinking>" + ("reasoning " * 5000) + "</thinking>\n" + self._BROKEN_ANSWER
        cap = self._run(run_exchange, [make_response(initial), make_response(self._ANSWER_ONLY)])

        transcript = cap.chat.call_args_list[-1].args[0]
        assistant_context = transcript[2]["content"]
        assert "reasoning reasoning" not in assistant_context
        assert len(assistant_context) <= ConnectionsGame.REPAIR_CONTEXT_MAX_CHARS + 120

    def test_missing_answer_gets_full_context_and_finish_instruction(self, run_exchange):
        initial = "<thinking>" + ("reasoning " * 5000)
        cap = self._run(run_exchange, [make_response(initial), make_response(self._ANSWER_ONLY)])

        transcript = cap.chat.call_args_list[-1].args[0]
        assert transcript[2]["role"] == "assistant"
        assert transcript[2]["content"] == initial.strip()
        followup = transcript[3]["content"]
        assert "done enough thinking" in followup
        assert "<traps>...</traps>" in followup
        assert "<confidence>...</confidence>" in followup
        assert cap.summary["total_score"] == 3

    def test_retries_are_capped_and_end_in_oneshot_invalid(self, run_exchange):
        """Three broken responses: two repairs, then the third is scored as-is."""
        cap = self._run(run_exchange, [make_response("no answer here")] * 3)

        assert cap.chat.call_count == 3
        # The missing-answer continuation stays in full-response mode through
        # the retry cap.
        assert [e["result"] for e in cap.exchanges] == [
            "LINT_RETRY_answer.missing_tag",
            "LINT_RETRY_answer.missing_tag",
            "ONESHOT_INVALID_MAX_5",
        ]
        assert cap.summary["invalid_responses"] == 1
        assert cap.summary["lint_retries"] == 2

    def test_lint_retries_reach_the_summary_and_the_scoring_exchange(self, run_exchange):
        cap = self._run(run_exchange, [make_response(self._BROKEN_ANSWER),
                                       make_response(self._ANSWER_ONLY)])

        assert cap.summary["lint_retries"] == 1
        # Only the scoring exchange carries the count; the repair turn names its rule
        assert cap.exchanges[-1]["lint_retries"] == 1
        assert cap.exchanges[0]["lint_rule"] == "answer.words_per_line"
        assert cap.exchanges[0]["lint_segment"] == "answer"
        assert "lint_retries" not in cap.exchanges[0]

    def test_repair_result_strings_avoid_the_aggregation_prefixes(self, run_exchange):
        """extract_summaries.py keys off ONESHOT% / INVALID% — a repair turn must
        match neither, or it would inflate max_score and invalid_responses."""
        cap = self._run(run_exchange, [make_response("no answer here")] * 3)

        for result in [e["result"] for e in cap.exchanges[:-1]]:
            assert result.startswith("LINT_RETRY_")
            assert not result.startswith("ONESHOT")
            assert not result.startswith("INVALID")
