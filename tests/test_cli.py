"""Tests for CLI interface."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from typer.testing import CliRunner

from connections_eval.cli import app, _display_summary, _validate_run_args


class TestCLI:
    """Test CLI interface."""
    
    def setup_method(self):
        """Set up test runner."""
        self.runner = CliRunner()
    
    def test_list_models(self):
        """Test listing available models."""
        result = self.runner.invoke(app, ["list-models"])
        assert result.exit_code == 0
        assert "grok-4.5" in result.stdout
        assert "o3" in result.stdout
        assert "gemini" in result.stdout
        assert "sonnet" in result.stdout
    
    def test_run_missing_model_and_interactive(self):
        """Test error when neither model nor interactive specified."""
        result = self.runner.invoke(app, ["run"])
        assert result.exit_code == 1
        assert "Either --model or --interactive must be specified" in result.stdout
    
    def test_run_both_model_and_interactive(self):
        """Test error when both model and interactive specified."""
        result = self.runner.invoke(app, ["run", "--model", "grok3", "--interactive"])
        assert result.exit_code == 1
        assert "Cannot specify both --model and --interactive" in result.stdout
    
    def test_run_unknown_model(self):
        """Test error with unknown model."""
        result = self.runner.invoke(app, ["run", "--model", "unknown"])
        assert result.exit_code == 2
        assert "Unknown model: unknown" in result.stdout
        assert "Available models:" in result.stdout

    def test_run_invalid_mode(self):
        """Test error when --mode is neither 'classic' nor 'oneshot'."""
        result = self.runner.invoke(app, ["run", "--mode", "bogus"])
        assert result.exit_code == 1
        assert "Invalid mode: bogus" in result.stdout

    def test_run_invalid_reasoning_effort(self):
        """Test error when --reasoning-effort is not a recognized level."""
        result = self.runner.invoke(app, ["run", "--reasoning-effort", "maximum"])
        assert result.exit_code == 1
        assert "Invalid reasoning effort: maximum" in result.stdout
    
    @patch.dict('os.environ', {}, clear=True)
    def test_run_missing_api_key(self):
        """Test error when API key missing."""
        result = self.runner.invoke(app, ["run", "--model", "kimi-k3"])
        assert result.exit_code == 1
        assert "OPENROUTER_API_KEY environment variable not set" in result.stdout
    
    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-key'})
    def test_run_missing_inputs_path(self):
        """Test error when inputs path doesn't exist."""
        result = self.runner.invoke(app, [
            "run", 
            "--model", "grok3",
            "--inputs-path", "/nonexistent"
        ])
        assert result.exit_code == 1
        assert "Inputs path does not exist" in result.stdout
    
    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-key'})
    @patch('connections_eval.cli.openrouter_adapter.assert_model_exists')
    @patch('connections_eval.cli.ConnectionsGame')
    def test_run_success(self, mock_game_class, mock_preflight):
        """Test successful run."""
        # Mock the game instance
        mock_game = MagicMock()
        mock_game.seed = 12345
        mock_game.MODEL_CONFIG = {
            "grok3": "x-ai/grok-3",
            "grok4": "x-ai/grok-4", 
            "o3": "openai/o3",
            "o4-mini": "openai/o4-mini",
            "gemini": "google/gemini-2.5-pro",
            "sonnet": "anthropic/claude-3.5-sonnet",
            "opus": "anthropic/claude-3-opus",
        }
        mock_game.run_evaluation.return_value = {
            "run_id": "test-run",
            "model": "grok3",
            "seed": 12345,
            "puzzles_attempted": 2,
            "puzzles_solved": 1,
            "total_guesses": 8,
            "correct_guesses": 4,
            "incorrect_guesses": 4,
            "invalid_responses": 0,
            "avg_time_sec": 15.5,
            "total_tokens": 1000,
            "token_count_method": "API"
        }
        mock_game_class.return_value = mock_game
        
        # Mock path checks and file operations
        with patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.mkdir'), \
             patch('builtins.open', MagicMock()):
            result = self.runner.invoke(app, [
                "run",
                "--model", "grok3",
                "--puzzles", "2"
            ])
        
        assert result.exit_code == 0
        assert "Starting Connections evaluation" in result.stdout
        assert "AI Model (grok3)" in result.stdout
        assert "Evaluation Results" in result.stdout
        
        # Verify game was called correctly
        mock_game.run_evaluation.assert_called_once_with(
            "grok3", max_puzzles=2, is_interactive=False, threads=8, puzzle_ids=None
        )


class TestOneshotSummaryDisplay:
    """_display_summary must render a one-shot summary without crashing."""

    def test_oneshot_summary_display(self):
        summary = {
            "run_id": "test-run-oneshot",
            "model": "grok3",
            "mode": "oneshot",
            "seed": 12345,
            "puzzles_attempted": 4,
            "puzzles_solved": 2,
            "total_guesses": 4,
            "correct_guesses": 6,
            "incorrect_guesses": 0,
            "invalid_responses": 0,
            "avg_time_sec": 3.2,
            "total_tokens": 800,
            "token_count_method": "API",
            "total_score": 12,
            "max_score": 20,
            "avg_score": 3.0,
        }
        # Should not raise, and should surface the one-shot-specific rows
        # (Total Score / Avg Score) instead of the classic "Guess Accuracy" row.
        _display_summary(summary, interactive=False)


class TestStructuredOutputFlag:
    """--structured-output is opt-in and reaches the game engine."""

    def setup_method(self):
        self.runner = CliRunner()

    _SUMMARY = {
        "run_id": "test-run",
        "model": "grok3",
        "seed": 12345,
        "structured_output": True,
        "puzzles_attempted": 1,
        "puzzles_solved": 1,
        "total_guesses": 4,
        "correct_guesses": 4,
        "incorrect_guesses": 0,
        "invalid_responses": 0,
        "avg_time_sec": 1.0,
        "total_tokens": 100,
        "token_count_method": "API",
    }

    def _invoke(self, mock_game_class, extra_args):
        mock_game = MagicMock()
        mock_game.seed = 12345
        mock_game.MODEL_CONFIG = {"grok3": "x-ai/grok-3"}
        mock_game.run_evaluation.return_value = dict(self._SUMMARY)
        mock_game_class.return_value = mock_game

        with patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.mkdir'), \
             patch('builtins.open', MagicMock()):
            result = self.runner.invoke(app, ["run", "--model", "grok3"] + extra_args)
        return result, mock_game_class

    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-key'}, clear=True)
    @patch('connections_eval.cli.openrouter_adapter.assert_model_exists')
    @patch('connections_eval.cli.ConnectionsGame')
    def test_flag_enables_structured_output(self, mock_game_class, mock_preflight):
        result, cls = self._invoke(mock_game_class, ["--structured-output"])
        assert result.exit_code == 0
        # call_args is the last construction — the real game, not the
        # validation probe built earlier in _validate_run_args.
        assert cls.call_args.kwargs["structured_output"] is True
        assert "Structured Output" in result.stdout

    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-key'}, clear=True)
    @patch('connections_eval.cli.openrouter_adapter.assert_model_exists')
    @patch('connections_eval.cli.ConnectionsGame')
    def test_default_is_off(self, mock_game_class, mock_preflight):
        result, cls = self._invoke(mock_game_class, [])
        assert result.exit_code == 0
        assert cls.call_args.kwargs["structured_output"] is False

    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'test-key'}, clear=True)
    @patch('connections_eval.cli.openrouter_adapter.assert_model_exists')
    @patch('connections_eval.cli.ConnectionsGame')
    def test_oneshot_structured_output(self, mock_game_class, mock_preflight):
        result, cls = self._invoke(
            mock_game_class, ["--mode", "oneshot", "--structured-output"])
        assert result.exit_code == 0
        assert cls.call_args.kwargs["structured_output"] is True
        assert cls.call_args.kwargs["mode"] == "oneshot"


class TestStructuredPromptFileValidation:
    """_validate_run_args checks the template core.py will actually load."""

    _INPUTS = Path(__file__).resolve().parent.parent / "inputs"

    @pytest.mark.parametrize("mode,structured,expected", [
        ("classic", False, "prompt_template.xml"),
        ("classic", True, "prompt_template_json.xml"),
        ("oneshot", False, "prompt_template_oneshot.xml"),
        ("oneshot", True, "prompt_template_oneshot_json.xml"),
    ])
    def test_effective_template_exists(self, mode, structured, expected):
        """Every mode/flag combination resolves to a template that exists."""
        assert (self._INPUTS / expected).exists()
        # Interactive run so no API key / model lookup is needed; validation
        # passes only because the resolved template is on disk.
        assert _validate_run_args(
            None, True, None, None, False, self._INPUTS,
            "prompt_template.xml", mode, None, structured,
        ) is None
