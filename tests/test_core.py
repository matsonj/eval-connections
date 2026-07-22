"""Tests for core game logic."""

import random
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from connections_eval.core import ConnectionsGame, GameState, Puzzle, PuzzleGroup, PuzzleResult, EvalStats
from connections_eval.adapters.openrouter_adapter import extract_provider_slug
from connections_eval.utils.tokens import extract_cache_info


def _make_test_groups():
    """Shared test fixture: four puzzle groups."""
    return [
        PuzzleGroup("Fruits", "green", ["APPLE", "BANANA", "CHERRY", "GRAPE"]),
        PuzzleGroup("Colors", "yellow", ["BLUE", "GREEN", "RED", "YELLOW"]),
        PuzzleGroup("Speed", "blue", ["FAST", "QUICK", "RAPID", "SWIFT"]),
        PuzzleGroup("Smart", "purple", ["BRIGHT", "CLEVER", "SMART", "WISE"]),
    ]


_TEST_WORDS = [
    "APPLE", "BANANA", "CHERRY", "GRAPE", "BLUE", "GREEN", "RED", "YELLOW",
    "FAST", "QUICK", "RAPID", "SWIFT", "BRIGHT", "CLEVER", "SMART", "WISE",
]


class TestConnectionsGame:
    """Test ConnectionsGame class."""

    @pytest.fixture
    def sample_puzzle(self):
        """Create a sample puzzle for testing."""
        return Puzzle(
            id=477, date="2024-09-30", difficulty=3.8,
            words=list(_TEST_WORDS), groups=_make_test_groups(),
        )

    @pytest.fixture
    def canonical_puzzle(self):
        """Create a sample canonical puzzle for testing."""
        return Puzzle(
            id=999, date="2024-12-01", difficulty=2.0,
            words=list(_TEST_WORDS), groups=_make_test_groups(),
            canonical=True,
        )

    @pytest.fixture
    def game_state(self, sample_puzzle):
        """Create a sample game state."""
        return GameState(
            puzzle=sample_puzzle,
            solved_groups=set(),
            guess_count=0,
            mistake_count=0,
            invalid_count=0,
            finished=False,
            won=False,
            start_time=None,
            end_time=None
        )

    @pytest.fixture
    def mock_game(self):
        """Create a mock game with proper initialization."""
        with patch.object(ConnectionsGame, '_load_puzzles', return_value=[]), \
             patch.object(ConnectionsGame, '_load_prompt_template', return_value=""), \
             patch.object(ConnectionsGame, '_load_model_mappings', return_value={"test-model": "test/model"}):
            return ConnectionsGame(Path("."), Path("."), verbose=False)

    def test_parse_response(self, mock_game):
        """Test response parsing."""
        # Normal case
        words = mock_game._parse_response("APPLE, BANANA, CHERRY, GRAPE")
        assert words == ["APPLE", "BANANA", "CHERRY", "GRAPE"]

        # Mixed case
        words = mock_game._parse_response("apple, Banana, CHERRY, grape")
        assert words == ["APPLE", "BANANA", "CHERRY", "GRAPE"]

        # Extra whitespace
        words = mock_game._parse_response(" APPLE , BANANA,  CHERRY , GRAPE ")
        assert words == ["APPLE", "BANANA", "CHERRY", "GRAPE"]

        # Wrong number of words
        words = mock_game._parse_response("APPLE, BANANA, CHERRY")
        assert words == ["APPLE", "BANANA", "CHERRY"]

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

    @pytest.fixture
    def mock_game(self):
        """Create a mock game with proper initialization."""
        with patch.object(ConnectionsGame, '_load_puzzles', return_value=[]), \
             patch.object(ConnectionsGame, '_load_prompt_template', return_value=""), \
             patch.object(ConnectionsGame, '_load_model_mappings', return_value={"test-model": "test/model"}):
            return ConnectionsGame(Path("."), Path("."), verbose=False)

    @pytest.fixture
    def sample_puzzle(self):
        return Puzzle(
            id=477, date="2024-09-30", difficulty=3.8,
            words=list(_TEST_WORDS), groups=_make_test_groups(),
        )

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
        assert mock_game._score_oneshot(sample_puzzle, groups) == (0, 0)

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
    """Test _score_oneshot for one-shot mode."""

    @pytest.fixture
    def mock_game(self):
        with patch.object(ConnectionsGame, '_load_puzzles', return_value=[]), \
             patch.object(ConnectionsGame, '_load_prompt_template', return_value=""), \
             patch.object(ConnectionsGame, '_load_model_mappings', return_value={"test-model": "test/model"}):
            return ConnectionsGame(Path("."), Path("."), verbose=False)

    @pytest.fixture
    def sample_puzzle(self):
        return Puzzle(
            id=477, date="2024-09-30", difficulty=3.8,
            words=list(_TEST_WORDS), groups=_make_test_groups(),
        )

    def test_all_four_correct(self, mock_game, sample_puzzle):
        groups = [
            ["APPLE", "BANANA", "CHERRY", "GRAPE"],
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
            ["BRIGHT", "CLEVER", "SMART", "WISE"],
        ]
        assert mock_game._score_oneshot(sample_puzzle, groups) == (4, 3)

    def test_two_correct_two_swapped(self, mock_game, sample_puzzle):
        """Two groups intact; the other two have words swapped between them so
        neither matches any puzzle group."""
        groups = [
            ["APPLE", "BANANA", "CHERRY", "GRAPE"],   # correct (Fruits)
            ["BLUE", "GREEN", "RED", "YELLOW"],       # correct (Colors)
            ["FAST", "QUICK", "SMART", "WISE"],       # 2 from Speed + 2 from Smart
            ["RAPID", "SWIFT", "BRIGHT", "CLEVER"],   # remaining 2 from Speed + 2 from Smart
        ]
        assert mock_game._score_oneshot(sample_puzzle, groups) == (2, 2)

    def test_one_correct(self, mock_game, sample_puzzle):
        """One group intact; the other 12 words are 3-cycled across the
        remaining three groups so none of them matches any puzzle group."""
        groups = [
            ["APPLE", "BANANA", "CHERRY", "GRAPE"],   # correct (Fruits)
            ["GREEN", "RED", "YELLOW", "BRIGHT"],     # Colors minus BLUE, plus BRIGHT
            ["QUICK", "RAPID", "SWIFT", "BLUE"],      # Speed minus FAST, plus BLUE
            ["CLEVER", "SMART", "WISE", "FAST"],      # Smart minus BRIGHT, plus FAST
        ]
        assert mock_game._score_oneshot(sample_puzzle, groups) == (1, 1)

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
        assert mock_game._score_oneshot(sample_puzzle, groups) == (0, 0)

    def test_wrong_word_not_in_puzzle(self, mock_game, sample_puzzle):
        """A word that doesn't belong to the puzzle at all is a structural failure."""
        groups = [
            ["APPLE", "BANANA", "CHERRY", "ORANGE"],  # ORANGE isn't in the puzzle
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
            ["BRIGHT", "CLEVER", "SMART", "WISE"],
        ]
        assert mock_game._score_oneshot(sample_puzzle, groups) == (0, 0)

    def test_duplicate_word_one_missing(self, mock_game, sample_puzzle):
        """A word appearing twice (with another word missing) is a structural failure."""
        groups = [
            ["APPLE", "APPLE", "CHERRY", "GRAPE"],  # APPLE duplicated, BANANA missing
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
            ["BRIGHT", "CLEVER", "SMART", "WISE"],
        ]
        assert mock_game._score_oneshot(sample_puzzle, groups) == (0, 0)

    def test_three_groups_only(self, mock_game, sample_puzzle):
        groups = [
            ["APPLE", "BANANA", "CHERRY", "GRAPE"],
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
        ]
        assert mock_game._score_oneshot(sample_puzzle, groups) == (0, 0)

    def test_five_groups(self, mock_game, sample_puzzle):
        groups = [
            ["APPLE", "BANANA", "CHERRY", "GRAPE"],
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
            ["BRIGHT", "CLEVER", "SMART", "WISE"],
            ["EXTRA", "GROUP", "NOT", "ALLOWED"],
        ]
        assert mock_game._score_oneshot(sample_puzzle, groups) == (0, 0)

    def test_group_with_three_words(self, mock_game, sample_puzzle):
        groups = [
            ["APPLE", "BANANA", "CHERRY"],  # only 3 words
            ["BLUE", "GREEN", "RED", "YELLOW"],
            ["FAST", "QUICK", "RAPID", "SWIFT"],
            ["BRIGHT", "CLEVER", "SMART", "WISE"],
        ]
        assert mock_game._score_oneshot(sample_puzzle, groups) == (0, 0)

    def test_word_order_within_group_irrelevant(self, mock_game, sample_puzzle):
        """Groups are compared as sets, so word order within a group doesn't matter."""
        groups = [
            ["GRAPE", "CHERRY", "BANANA", "APPLE"],
            ["YELLOW", "RED", "GREEN", "BLUE"],
            ["SWIFT", "RAPID", "QUICK", "FAST"],
            ["WISE", "SMART", "CLEVER", "BRIGHT"],
        ]
        assert mock_game._score_oneshot(sample_puzzle, groups) == (4, 3)


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

    _INPUTS = Path(__file__).resolve().parent.parent / "inputs"

    def test_oneshot_mode_loads_oneshot_template(self):
        game = ConnectionsGame(self._INPUTS, Path("logs"), mode="oneshot")
        assert game.mode == "oneshot"
        assert "<answer>" in game.prompt_template
        assert "<guess>" not in game.prompt_template

    def test_default_mode_loads_classic_template(self):
        game = ConnectionsGame(self._INPUTS, Path("logs"))
        assert game.mode == "classic"
        assert "<guess>" in game.prompt_template
        assert "<answer>" not in game.prompt_template


class TestOneshotEndToEnd:
    """run_evaluation drives the one-shot path end to end (mocked adapter)."""

    _INPUTS = Path(__file__).resolve().parent.parent / "inputs"

    def _make_game(self, tmp_path, puzzle, mode="oneshot"):
        """Game with a single synthetic puzzle; real prompt template from inputs/."""
        with patch.object(ConnectionsGame, '_load_puzzles', return_value=[puzzle]), \
             patch.object(ConnectionsGame, '_load_model_mappings', return_value={"test-model": "test/model"}):
            return ConnectionsGame(self._INPUTS, tmp_path, seed=42, mode=mode)

    @staticmethod
    def _make_puzzle():
        return Puzzle(
            id=477, date="2024-09-30", difficulty=3.8,
            words=list(_TEST_WORDS), groups=_make_test_groups(),
        )

    @staticmethod
    def _mock_response(content):
        return {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }

    def test_perfect_submission_summary(self, tmp_path):
        """A perfect one-shot answer yields score 5 and a solved puzzle."""
        puzzle = self._make_puzzle()
        game = self._make_game(tmp_path, puzzle)
        answer = "<answer>\n" + "\n".join(
            ", ".join(g.words) for g in puzzle.groups
        ) + "\n</answer>"

        with patch("connections_eval.core.openrouter_adapter.chat",
                   return_value=self._mock_response(answer)):
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

    def test_invalid_submission_summary(self, tmp_path):
        """An unparseable response scores 0 and counts as invalid."""
        puzzle = self._make_puzzle()
        game = self._make_game(tmp_path, puzzle)

        with patch("connections_eval.core.openrouter_adapter.chat",
                   return_value=self._mock_response("I refuse to answer in the requested format.")):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["mode"] == "oneshot"
        assert summary["puzzles_solved"] == 0
        assert summary["total_score"] == 0
        assert summary["max_score"] == 3
        assert summary["avg_score"] == 0.0
        assert summary["invalid_responses"] == 1
        assert summary["incorrect_guesses"] == 0

    def test_trap_bonus_end_to_end(self, tmp_path):
        """Perfect answer + correct trap claim on an annotated puzzle scores 5/5."""
        puzzle = self._make_puzzle()
        # Annotate a superset trap: FAST/QUICK/RAPID/SWIFT (real group) + SMART
        # would be wrong — use a synthetic 4-set crossing two groups instead.
        puzzle.trap_groups = [["FAST", "QUICK", "RAPID", "SMART"]]
        game = self._make_game(tmp_path, puzzle)
        answer = "<answer>\n" + "\n".join(
            ", ".join(g.words) for g in puzzle.groups
        ) + "\n</answer>\n<traps>\nFAST, QUICK, RAPID, SMART\n</traps>"

        with patch("connections_eval.core.openrouter_adapter.chat",
                   return_value=self._mock_response(answer)):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["total_score"] == 5
        assert summary["max_score"] == 5
        assert summary["total_trap_bonus"] == 2
        assert summary["puzzles_solved"] == 1

    def test_false_trap_claim_voids_bonus(self, tmp_path):
        """A false trap claim scores base only, even alongside a correct one."""
        puzzle = self._make_puzzle()
        puzzle.trap_groups = [["FAST", "QUICK", "RAPID", "SMART"]]
        game = self._make_game(tmp_path, puzzle)
        answer = "<answer>\n" + "\n".join(
            ", ".join(g.words) for g in puzzle.groups
        ) + "\n</answer>\n<traps>\nFAST, QUICK, RAPID, SMART\nAPPLE, BLUE, FAST, WISE\n</traps>"

        with patch("connections_eval.core.openrouter_adapter.chat",
                   return_value=self._mock_response(answer)):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["total_score"] == 3
        assert summary["max_score"] == 5
        assert summary["total_trap_bonus"] == 0

    def test_na_on_trapless_puzzle_earns_bonus(self, tmp_path):
        """Explicit N/A on a reviewed trap-free puzzle earns the +2."""
        puzzle = self._make_puzzle()
        puzzle.trap_groups = []  # reviewed, no traps
        game = self._make_game(tmp_path, puzzle)
        answer = "<answer>\n" + "\n".join(
            ", ".join(g.words) for g in puzzle.groups
        ) + "\n</answer>\n<traps>\nN/A\n</traps>"

        with patch("connections_eval.core.openrouter_adapter.chat",
                   return_value=self._mock_response(answer)):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["total_score"] == 5
        assert summary["max_score"] == 5
        assert summary["total_trap_bonus"] == 2

    def test_api_error_still_counts_max_score(self, tmp_path):
        """An API-error puzzle contributes its per-puzzle max (annotated -> 5)
        so a partially-failed run's max_score stays honest."""
        puzzle = self._make_puzzle()
        puzzle.trap_groups = [["FAST", "QUICK", "RAPID", "SMART"]]
        game = self._make_game(tmp_path, puzzle)

        with patch("connections_eval.core.openrouter_adapter.chat",
                   side_effect=RuntimeError("boom")):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["mode"] == "oneshot"
        assert summary["puzzles_attempted"] == 1
        assert summary["total_score"] == 0
        assert summary["max_score"] == 5
        assert summary["total_trap_bonus"] == 0

    def test_mode_override_mismatch_raises(self, tmp_path):
        """run_evaluation(mode=...) must match the mode the game was built with,
        since the prompt template is selected at construction time."""
        puzzle = self._make_puzzle()
        game = self._make_game(tmp_path, puzzle, mode="classic")

        with pytest.raises(ValueError, match="conflicts with game mode"):
            game.run_evaluation("test-model", puzzle_ids=[477], mode="oneshot")


class TestOneshotTraps:
    """Trap claim parsing and scoring rules."""

    @pytest.fixture
    def mock_game(self):
        with patch.object(ConnectionsGame, '_load_puzzles', return_value=[]), \
             patch.object(ConnectionsGame, '_load_prompt_template', return_value=""), \
             patch.object(ConnectionsGame, '_load_model_mappings', return_value={"test-model": "test/model"}):
            return ConnectionsGame(Path("."), Path("."), verbose=False)

    @pytest.fixture
    def trap_puzzle(self):
        """Puzzle with one 4-set trap and one 5-word superset trap."""
        return Puzzle(
            id=477, date="2024-09-30", difficulty=3.8,
            words=list(_TEST_WORDS), groups=_make_test_groups(),
            trap_groups=[
                ["FAST", "QUICK", "RAPID", "SMART"],           # 4-set
                ["BRIGHT", "CLEVER", "SMART", "WISE", "QUICK"] # superset: real group + QUICK
            ],
        )

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

    def test_correct_4set_claim(self, mock_game, trap_puzzle):
        assert mock_game._score_trap_claims(trap_puzzle, [["FAST", "QUICK", "RAPID", "SMART"]]) == 2

    def test_subset_of_superset_claim(self, mock_game, trap_puzzle):
        # Any 4-subset of the 5-word superset that isn't the real group scores
        assert mock_game._score_trap_claims(trap_puzzle, [["BRIGHT", "CLEVER", "SMART", "QUICK"]]) == 2

    def test_real_group_subset_of_superset_rejected(self, mock_game, trap_puzzle):
        # The real Smart group is inside the superset but is NOT a trap
        assert mock_game._score_trap_claims(trap_puzzle, [["BRIGHT", "CLEVER", "SMART", "WISE"]]) == 0

    def test_false_claim_voids_even_with_correct_one(self, mock_game, trap_puzzle):
        claims = [["FAST", "QUICK", "RAPID", "SMART"], ["APPLE", "BLUE", "FAST", "WISE"]]
        assert mock_game._score_trap_claims(trap_puzzle, claims) == 0

    def test_two_correct_claims(self, mock_game, trap_puzzle):
        claims = [["FAST", "QUICK", "RAPID", "SMART"], ["BRIGHT", "CLEVER", "WISE", "QUICK"]]
        assert mock_game._score_trap_claims(trap_puzzle, claims) == 2

    def test_extra_claims_ignored(self, mock_game, trap_puzzle):
        # Only the first 2 distinct claims are judged; a bogus 3rd is ignored
        claims = [["FAST", "QUICK", "RAPID", "SMART"],
                  ["BRIGHT", "CLEVER", "WISE", "QUICK"],
                  ["APPLE", "BLUE", "FAST", "WISE"]]
        assert mock_game._score_trap_claims(trap_puzzle, claims) == 2

    def test_duplicate_claims_deduped(self, mock_game, trap_puzzle):
        claims = [["FAST", "QUICK", "RAPID", "SMART"], ["SMART", "RAPID", "QUICK", "FAST"]]
        assert mock_game._score_trap_claims(trap_puzzle, claims) == 2

    def test_na_correct_on_trapless(self, mock_game):
        p = Puzzle(id=1, date="", difficulty=1.0, words=list(_TEST_WORDS),
                   groups=_make_test_groups(), trap_groups=[])
        assert mock_game._score_trap_claims(p, []) == 2

    def test_na_wrong_when_traps_exist(self, mock_game, trap_puzzle):
        assert mock_game._score_trap_claims(trap_puzzle, []) == 0

    def test_no_claim_no_bonus(self, mock_game, trap_puzzle):
        assert mock_game._score_trap_claims(trap_puzzle, None) == 0

    def test_unreviewed_puzzle_inactive(self, mock_game):
        p = Puzzle(id=1, date="", difficulty=1.0, words=list(_TEST_WORDS),
                   groups=_make_test_groups())  # trap_groups=None
        assert mock_game._score_trap_claims(p, [["FAST", "QUICK", "RAPID", "SMART"]]) == 0

    def test_wrong_size_claim_voids(self, mock_game, trap_puzzle):
        assert mock_game._score_trap_claims(trap_puzzle, [["FAST", "QUICK", "RAPID"]]) == 0

    def test_full_superset_claim_scores(self, mock_game, trap_puzzle):
        """A 5-word claim matching the annotated superset exactly scores."""
        claims = [["BRIGHT", "CLEVER", "SMART", "WISE", "QUICK"]]
        assert mock_game._score_trap_claims(trap_puzzle, claims) == 2

    def test_oversized_claim_not_subset_voids(self, mock_game, trap_puzzle):
        """A 5-word claim that isn't inside any annotated trap voids."""
        claims = [["FAST", "QUICK", "RAPID", "SMART", "APPLE"]]
        assert mock_game._score_trap_claims(trap_puzzle, claims) == 0

    def test_canonical_yaml_traps_load(self):
        """The real YAML annotations load into Puzzle.trap_groups."""
        inputs = Path(__file__).resolve().parent.parent / "inputs"
        game = ConnectionsGame(inputs, Path("logs"))
        by_id = {p.id: p for p in game.puzzles}
        assert by_id[246].trap_groups is not None and len(by_id[246].trap_groups) == 3
        assert by_id[837].trap_groups == []  # reviewed, trap-free
        assert by_id[828].trap_groups == []
        # Superset annotation present (476 OVER-___ 5-set)
        assert any(len(t) == 5 for t in by_id[476].trap_groups)
        # Non-canonical puzzles are unreviewed
        assert all(p.trap_groups is None for p in game.puzzles if not p.canonical)


class TestOneshotFallbackPunctuation:
    """Tagless fallback parsing keeps hyphenated/apostrophe words intact."""

    @pytest.fixture
    def mock_game(self):
        with patch.object(ConnectionsGame, '_load_puzzles', return_value=[]), \
             patch.object(ConnectionsGame, '_load_prompt_template', return_value=""), \
             patch.object(ConnectionsGame, '_load_model_mappings', return_value={"test-model": "test/model"}):
            return ConnectionsGame(Path("."), Path("."), verbose=False)

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

    def test_anthropic_provider(self):
        assert extract_provider_slug("anthropic/claude-sonnet-4") == "anthropic"

    def test_openai_provider(self):
        assert extract_provider_slug("openai/o3") == "openai"

    def test_google_provider(self):
        assert extract_provider_slug("google/gemini-2.5-pro") == "google-ai-studio"

    def test_xai_provider(self):
        assert extract_provider_slug("x-ai/grok-3") == "xai"

    def test_sonnet_5_overridden_to_bedrock(self):
        """TEMPORARY: claude-sonnet-5 400s on the Anthropic route (deprecated
        top_p injected via reasoning.effort), so it's pinned to Bedrock instead.
        Remove the override — and this test — once OpenRouter fixes that route."""
        assert extract_provider_slug("anthropic/claude-sonnet-5") == "amazon-bedrock"

    def test_other_anthropic_models_not_overridden(self):
        """The sonnet-5 override must not leak to sibling Anthropic models."""
        assert extract_provider_slug("anthropic/claude-sonnet-4.6") == "anthropic"
        assert extract_provider_slug("anthropic/claude-opus-4.8") == "anthropic"

    def test_deepseek_skipped(self):
        """DeepSeek models are hosted by third parties; pinning is skipped."""
        assert extract_provider_slug("deepseek/deepseek-r1-0528") is None

    def test_meta_llama_skipped(self):
        """Meta-Llama models are hosted by third parties; pinning is skipped."""
        assert extract_provider_slug("meta-llama/llama-3.3-70b-instruct") is None

    def test_qwen_skipped(self):
        """Qwen models are hosted by third parties; pinning is skipped."""
        assert extract_provider_slug("qwen/qwen3-30b-a3b-instruct-2507") is None

    def test_unknown_provider_returns_none(self):
        assert extract_provider_slug("unknown/some-model") is None

    def test_empty_string_returns_none(self):
        assert extract_provider_slug("") is None


class TestCacheInfo:
    """Test cache info extraction."""

    def test_extract_cache_info_present(self):
        response = {
            "usage": {
                "prompt_tokens_details": {
                    "cached_tokens": 500,
                },
                "cache_discount": 0.5,
            }
        }
        info = extract_cache_info(response)
        assert info["cached_tokens"] == 500
        assert info["cache_discount"] == 0.5

    def test_extract_cache_info_absent(self):
        response = {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        info = extract_cache_info(response)
        assert info["cached_tokens"] is None
        assert info["cache_discount"] is None

    def test_extract_cache_info_empty(self):
        info = extract_cache_info({})
        assert info["cached_tokens"] is None
        assert info["cache_discount"] is None


class TestChatProviderParam:
    """Test that chat() passes provider to payload."""

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_chat_without_provider(self, mock_key, mock_post):
        """Provider key should not appear when provider is None."""
        from connections_eval.adapters.openrouter_adapter import chat

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_post.return_value = mock_response

        chat([{"role": "user", "content": "test"}], "openai/o3", provider=None)

        payload = mock_post.call_args[1]["json"]
        assert "provider" not in payload

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_chat_with_provider(self, mock_key, mock_post):
        """Provider key should be set when provider is given."""
        from connections_eval.adapters.openrouter_adapter import chat

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_post.return_value = mock_response

        chat([{"role": "user", "content": "test"}], "openai/o3", provider="openai")

        payload = mock_post.call_args[1]["json"]
        assert payload["provider"] == {"order": ["openai"], "allow_fallbacks": False}

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_chat_without_session_id(self, mock_key, mock_post):
        """session_id key should not appear when session_id is None."""
        from connections_eval.adapters.openrouter_adapter import chat

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_post.return_value = mock_response

        chat([{"role": "user", "content": "test"}], "openrouter/fusion")

        payload = mock_post.call_args[1]["json"]
        assert "session_id" not in payload

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def test_chat_with_session_id(self, mock_key, mock_post):
        """session_id should be set top-level for sticky routing on cloaked models."""
        from connections_eval.adapters.openrouter_adapter import chat

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_post.return_value = mock_response

        chat([{"role": "user", "content": "test"}], "openrouter/fusion", session_id="T314:run1")

        payload = mock_post.call_args[1]["json"]
        assert payload["session_id"] == "T314:run1"
        # Cloaked model has no pinnable slug, so no provider order is forced.
        assert "provider" not in payload


class TestRankSessionIsolation:
    """session_id passed to the adapter must be unique per ranking attempt so
    repeated trials of one puzzle don't share a sticky-routing session."""

    _INPUTS = Path(__file__).resolve().parent.parent / "inputs"

    def _build_game(self):
        game = ConnectionsGame(self._INPUTS, Path("logs"))
        game.run_id = "rank_test-model"
        game.logger = MagicMock()
        return game

    def _capture_session_ids(self, attempt):
        """Run one puzzle with a mocked adapter and return the session_ids the
        game passed to adapter.chat. The mock returns an unparseable guess so the
        game ends after MAX_INVALID turns regardless of which puzzle is chosen."""
        captured = []

        def fake_chat(messages, model_id, provider=None, session_id=None, reasoning_effort=None):
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
        game = ConnectionsGame(self._INPUTS, Path("logs"))
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


class TestUtilities:
    """Test utility functions."""

    def test_timer(self):
        """Test Timer utility."""
        from connections_eval.utils.timing import Timer
        import time

        with Timer() as timer:
            time.sleep(0.1)

        assert 0.09 < timer.elapsed_seconds < 0.15
        assert 90 < timer.elapsed_ms < 150

    def test_token_counting(self):
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


class TestReasoningEffort:
    """reasoning_effort plumbs through to the OpenRouter request payload."""

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    @patch("connections_eval.adapters.openrouter_adapter._get_api_key", return_value="test-key")
    def _call_chat(self, mock_key, mock_post, model, **kwargs):
        from connections_eval.adapters.openrouter_adapter import chat

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_post.return_value = mock_response

        chat([{"role": "user", "content": "test"}], model, **kwargs)
        return mock_post.call_args.kwargs["json"]

    def test_thinking_model_defaults_to_minimal(self):
        # openai/o3 is in the thinking section of model_mappings.yml
        payload = self._call_chat(model="openai/o3")
        assert payload["reasoning"] == {"effort": "minimal"}

    def test_thinking_model_effort_override(self):
        payload = self._call_chat(model="openai/o3", reasoning_effort="high")
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
        import requests
        from connections_eval.adapters.openrouter_adapter import chat
        from connections_eval.utils import retry as retry_mod

        monkeypatch.setattr(retry_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(retry_mod.random, "uniform", lambda a, b: 0.0)

        bad_response = MagicMock()
        bad_response.ok = True
        bad_response.json.return_value = {"error": {"message": "upstream throttled"}}
        bad_response.raise_for_status.return_value = None

        good_response = MagicMock()
        good_response.ok = True
        good_response.json.return_value = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        good_response.raise_for_status.return_value = None

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

        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        mock_post.return_value = mock_response

        result = chat([{"role": "user", "content": "test"}], "openai/o3", provider=None)

        assert result["_backoff_sec"] == 0.0
