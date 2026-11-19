"""Shared fixtures and fake-data builders for the test suite.

These consolidate scaffolding that used to be copy-pasted per test class in
tests/test_core.py: a fake OpenRouter response builder, a puzzle factory, a
`ConnectionsGame` factory with its loaders patched, and a `chat()` patcher.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from connections_eval.core import ConnectionsGame, Puzzle, PuzzleGroup

INPUTS = Path(__file__).resolve().parent.parent / "inputs"

TEST_WORDS = [
    "APPLE", "BANANA", "CHERRY", "GRAPE", "BLUE", "GREEN", "RED", "YELLOW",
    "FAST", "QUICK", "RAPID", "SWIFT", "BRIGHT", "CLEVER", "SMART", "WISE",
]


def make_test_groups():
    """Four puzzle groups shared across tests."""
    return [
        PuzzleGroup("Fruits", "green", ["APPLE", "BANANA", "CHERRY", "GRAPE"]),
        PuzzleGroup("Colors", "yellow", ["BLUE", "GREEN", "RED", "YELLOW"]),
        PuzzleGroup("Speed", "blue", ["FAST", "QUICK", "RAPID", "SWIFT"]),
        PuzzleGroup("Smart", "purple", ["BRIGHT", "CLEVER", "SMART", "WISE"]),
    ]


def make_puzzle(id=477, date="2024-09-30", difficulty=3.8, canonical=False,
                trap_groups=None, words=None, groups=None):
    """Build a Puzzle. Defaults match the long-standing "sample puzzle" used
    throughout the suite; override only what a test cares about."""
    return Puzzle(
        id=id, date=date, difficulty=difficulty,
        words=list(words) if words is not None else list(TEST_WORDS),
        groups=groups if groups is not None else make_test_groups(),
        canonical=canonical, trap_groups=trap_groups,
    )


def make_response(content, *, prompt_tokens=100, completion_tokens=50,
                   finish_reason="stop", native_finish_reason=None,
                   provider=None, cost=None, cached=None, is_byok=None):
    """Build a fake OpenRouter chat-completion JSON body.

    cost, when given, also fills cost_details.upstream_inference_cost (2x
    cost, matching a BYOK upstream markup) and sets usage.is_byok, unless
    is_byok is given explicitly. cached fills prompt_tokens_details.cached_tokens.
    """
    usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if cost is not None:
        usage["cost"] = cost
        usage["cost_details"] = {"upstream_inference_cost": cost * 2}
        usage["is_byok"] = True if is_byok is None else is_byok
    if cached is not None:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}

    choice = {"message": {"content": content}, "finish_reason": finish_reason}
    if native_finish_reason is not None:
        choice["native_finish_reason"] = native_finish_reason

    response = {"choices": [choice], "usage": usage}
    if provider is not None:
        response["provider"] = provider
    return response


@pytest.fixture
def sample_puzzle():
    """The default four-group sample puzzle (id=477, not canonical)."""
    return make_puzzle()


@pytest.fixture
def canonical_puzzle():
    """A canonical variant of the sample puzzle (id=999)."""
    return make_puzzle(id=999, date="2024-12-01", difficulty=2.0, canonical=True)


@pytest.fixture
def mock_game():
    """A ConnectionsGame with all loaders patched to be inert: no puzzles, no
    prompt template, one test-model mapping. For exercising game-logic methods
    directly (parsing, scoring, validation) without touching disk."""
    with patch.object(ConnectionsGame, "_load_puzzles", return_value=[]), \
         patch.object(ConnectionsGame, "_load_prompt_template", return_value=""), \
         patch.object(ConnectionsGame, "_load_model_mappings",
                      return_value={"test-model": "test/model"}):
        return ConnectionsGame(Path("."), Path("."), verbose=False)


@pytest.fixture
def make_game():
    """Factory fixture: make_game(tmp_path, puzzle=None, puzzles=None,
    mode="classic", mappings=None, seed=42, **game_kwargs) builds a
    ConnectionsGame against the real inputs/ prompt templates, with
    _load_puzzles and _load_model_mappings patched to the given values."""
    def _make(tmp_path, puzzle=None, puzzles=None, mode="classic",
              mappings=None, seed=42, **game_kwargs):
        if puzzles is None:
            puzzles = [] if puzzle is None else [puzzle]
        with patch.object(ConnectionsGame, "_load_puzzles", return_value=puzzles), \
             patch.object(ConnectionsGame, "_load_model_mappings",
                          return_value=mappings or {"test-model": "test/model"}):
            return ConnectionsGame(INPUTS, tmp_path, seed=seed, mode=mode, **game_kwargs)
    return _make


@pytest.fixture
def patch_chat():
    """Factory fixture: patch_chat(responses) returns a patch() context
    manager for connections_eval.core.openrouter_adapter.chat (yields the
    mock, which records every call).

    `responses` may be:
      - a dict: the same response is returned for every call (return_value);
      - a list: responses are consumed in order (side_effect);
      - an Exception (instance or class): raised on every call;
      - a callable: used directly as side_effect.
    A plain dict can't be passed as side_effect (mock would iterate its keys),
    hence the special case.
    """
    def _patch(responses):
        target = "connections_eval.core.openrouter_adapter.chat"
        if isinstance(responses, dict):
            return patch(target, return_value=responses)
        return patch(target, side_effect=responses)
    return _patch
