"""Tests for core game logic."""

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

        assert result == "INCORRECT. 3 INCORRECT GUESSES REMAINING"
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
            assert result == f"INCORRECT. {expected_remaining} INCORRECT GUESSES REMAINING"

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
