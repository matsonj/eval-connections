"""Tests for core game logic."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from connections_eval.core import ConnectionsGame, GameState, Puzzle, PuzzleGroup


class TestConnectionsGame:
    """Test ConnectionsGame class."""
    
    @pytest.fixture
    def sample_puzzle(self):
        """Create a sample puzzle for testing."""
        groups = [
            PuzzleGroup("Fruits", "green", ["APPLE", "BANANA", "CHERRY", "GRAPE"]),
            PuzzleGroup("Colors", "yellow", ["BLUE", "GREEN", "RED", "YELLOW"]),
            PuzzleGroup("Speed", "blue", ["FAST", "QUICK", "RAPID", "SWIFT"]),
            PuzzleGroup("Smart", "purple", ["BRIGHT", "CLEVER", "SMART", "WISE"])
        ]
        return Puzzle(
            id=477,
            date="2024-09-30",
            difficulty=3.8,
            words=["APPLE", "BANANA", "CHERRY", "GRAPE", "BLUE", "GREEN", "RED", "YELLOW",
                   "FAST", "QUICK", "RAPID", "SWIFT", "BRIGHT", "CLEVER", "SMART", "WISE"],
            groups=groups
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
