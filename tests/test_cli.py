"""Tests for CLI interface."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from typer.testing import CliRunner

from connections_eval.cli import app


class TestCLI:
    """Test CLI interface."""
    
    def setup_method(self):
        """Set up test runner."""
        self.runner = CliRunner()
    
    def test_list_models(self):
        """Test listing available models."""
        result = self.runner.invoke(app, ["list-models"])
        assert result.exit_code == 0
        assert "grok3" in result.stdout
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
    
    @patch.dict('os.environ', {}, clear=True)
    def test_run_missing_api_key(self):
        """Test error when API key missing."""
        result = self.runner.invoke(app, ["run", "--model", "grok3"])
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
    @patch('connections_eval.cli.ConnectionsGame')
    def test_run_success(self, mock_game_class):
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
        mock_game.run_evaluation.assert_called_once_with("grok3", 2, False)
