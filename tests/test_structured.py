"""Tests for opt-in structured (JSON schema) output."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from connections_eval.core import ConnectionsGame, Puzzle, PuzzleGroup
from connections_eval.structured import (
    CLASSIC_SCHEMA,
    ONESHOT_SCHEMA,
    build_response_format,
    render_json_content,
)

_INPUTS = Path(__file__).resolve().parent.parent / "inputs"


def _make_test_groups():
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


class TestSchemas:
    """The response_format blocks OpenRouter receives."""

    def test_oneshot_envelope(self):
        rf = build_response_format("oneshot")
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "connections_oneshot"
        assert rf["json_schema"]["strict"] is True
        assert rf["json_schema"]["schema"] is ONESHOT_SCHEMA

    def test_classic_envelope(self):
        rf = build_response_format("classic")
        assert rf["json_schema"]["name"] == "connections_classic"
        assert rf["json_schema"]["strict"] is True
        assert rf["json_schema"]["schema"] is CLASSIC_SCHEMA

    def test_oneshot_is_four_groups_of_four(self):
        answer = ONESHOT_SCHEMA["properties"]["answer"]
        assert answer["minItems"] == answer["maxItems"] == 4
        inner = answer["items"]
        assert inner["minItems"] == inner["maxItems"] == 4
        assert inner["items"] == {"type": "string"}

    def test_oneshot_required_keys_and_closed_object(self):
        assert ONESHOT_SCHEMA["additionalProperties"] is False
        assert set(ONESHOT_SCHEMA["required"]) == {
            "thinking", "answer", "traps", "confidence"
        }
        assert set(ONESHOT_SCHEMA["properties"]) == set(ONESHOT_SCHEMA["required"])

    def test_oneshot_traps_allows_zero_or_four(self):
        traps = ONESHOT_SCHEMA["properties"]["traps"]
        assert traps["minItems"] == 0
        assert traps["maxItems"] == 4
        assert "empty array" in traps["description"].lower()

    def test_classic_guess_is_four_strings(self):
        guess = CLASSIC_SCHEMA["properties"]["guess"]
        assert guess["minItems"] == guess["maxItems"] == 4
        assert guess["items"] == {"type": "string"}
        assert CLASSIC_SCHEMA["additionalProperties"] is False
        assert set(CLASSIC_SCHEMA["required"]) == {"thinking", "guess", "confidence"}

    def test_confidence_is_a_bounded_number(self):
        for schema in (ONESHOT_SCHEMA, CLASSIC_SCHEMA):
            conf = schema["properties"]["confidence"]
            assert conf["type"] == "number"
            assert conf["minimum"] == 0
            assert conf["maximum"] == 1

    def test_no_unsupported_keywords_anywhere(self):
        """Conservative subset only — enums/anyOf/pattern break some providers."""
        def walk(node):
            if isinstance(node, dict):
                for banned in ("enum", "anyOf", "oneOf", "allOf", "pattern", "$ref"):
                    assert banned not in node, f"{banned} present in schema"
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(ONESHOT_SCHEMA)
        walk(CLASSIC_SCHEMA)


class TestOneshotRendering:
    """JSON -> the XML-ish text the existing one-shot parsers understand."""

    def test_full_payload(self):
        payload = json.dumps({
            "thinking": "these are the groups",
            "answer": [
                ["APPLE", "BANANA", "CHERRY", "GRAPE"],
                ["BLUE", "GREEN", "RED", "YELLOW"],
                ["FAST", "QUICK", "RAPID", "SWIFT"],
                ["BRIGHT", "CLEVER", "SMART", "WISE"],
            ],
            "traps": ["FAST", "QUICK", "BRIGHT", "CLEVER"],
            "confidence": 0.8,
        })
        assert render_json_content(payload, "oneshot") == (
            "<thinking>these are the groups</thinking>\n"
            "\n"
            "<answer>\n"
            "APPLE, BANANA, CHERRY, GRAPE\n"
            "BLUE, GREEN, RED, YELLOW\n"
            "FAST, QUICK, RAPID, SWIFT\n"
            "BRIGHT, CLEVER, SMART, WISE\n"
            "</answer>\n"
            "\n"
            "<traps>\n"
            "FAST, QUICK, BRIGHT, CLEVER\n"
            "</traps>\n"
            "\n"
            "<confidence>\n"
            "0.8\n"
            "</confidence>"
        )

    def test_empty_traps_render_as_na(self):
        payload = json.dumps({
            "thinking": "no trap here",
            "answer": [["A", "B", "C", "D"]] * 4,
            "traps": [],
            "confidence": 0.5,
        })
        assert "<traps>\nN/A\n</traps>" in render_json_content(payload, "oneshot")

    def test_lowercase_and_whitespace_are_normalized(self):
        payload = json.dumps({
            "thinking": "  padded  ",
            "answer": [["  apple ", "Banana", "cherry", "GRAPE"]] + [["A", "B", "C", "D"]] * 3,
            "traps": [" fast ", "quick", "BRIGHT", "clever"],
            "confidence": 0.25,
        })
        text = render_json_content(payload, "oneshot")
        assert "APPLE, BANANA, CHERRY, GRAPE" in text
        assert "FAST, QUICK, BRIGHT, CLEVER" in text
        assert text.startswith("<thinking>padded</thinking>")

    def test_missing_optional_blocks_are_omitted(self):
        payload = json.dumps({"answer": [["A", "B", "C", "D"]] * 4})
        text = render_json_content(payload, "oneshot")
        assert text == "<answer>\nA, B, C, D\nA, B, C, D\nA, B, C, D\nA, B, C, D\n</answer>"

    def test_code_fenced_json_is_still_rendered(self):
        payload = "```json\n" + json.dumps({
            "answer": [["A", "B", "C", "D"]] * 4, "traps": [], "confidence": 1,
        }) + "\n```"
        assert render_json_content(payload, "oneshot").startswith("<answer>")

    def test_rendered_output_round_trips_through_the_parsers(self, tmp_path):
        """The rendered text must parse identically to a native XML response."""
        game = _make_game(tmp_path, _make_puzzle(), structured_output=True)
        payload = json.dumps({
            "thinking": "reasoning",
            "answer": [g.words for g in _make_test_groups()],
            "traps": ["FAST", "QUICK", "BRIGHT", "CLEVER"],
            "confidence": 0.9,
        })
        text = render_json_content(payload, "oneshot")

        assert game._parse_oneshot_response(text) == [g.words for g in _make_test_groups()]
        assert game._parse_oneshot_traps(text) == [["FAST", "QUICK", "BRIGHT", "CLEVER"]]
        assert game._parse_structured_response(text)["confidence"] == "0.9"


class TestClassicRendering:
    """JSON -> the XML-ish text the existing classic parsers understand."""

    def test_full_payload(self):
        payload = json.dumps({
            "thinking": "fruit first",
            "guess": ["apple", "BANANA", " cherry", "grape "],
            "confidence": 0.6,
        })
        assert render_json_content(payload, "classic") == (
            "<thinking>fruit first</thinking>\n"
            "\n"
            "<guess>\n"
            "APPLE, BANANA, CHERRY, GRAPE\n"
            "</guess>\n"
            "\n"
            "<confidence>\n"
            "0.6\n"
            "</confidence>"
        )

    def test_rendered_output_round_trips_through_the_parsers(self, tmp_path):
        game = _make_game(tmp_path, _make_puzzle(), mode="classic", structured_output=True)
        text = render_json_content(json.dumps({
            "thinking": "t", "guess": ["APPLE", "BANANA", "CHERRY", "GRAPE"],
            "confidence": 0.6,
        }), "classic")
        assert game._parse_response(text) == ["APPLE", "BANANA", "CHERRY", "GRAPE"]


class TestPassthrough:
    """Anything that isn't the expected JSON object is left exactly as-is."""

    @pytest.mark.parametrize("content", [
        "I refuse to answer in the requested format.",
        "<answer>\nAPPLE, BANANA, CHERRY, GRAPE\n</answer>",
        "{not json at all",
        '["a", "list", "not", "an", "object"]',
        '{"thinking": "no answer key here", "confidence": 0.5}',
        '"just a json string"',
        "",
    ])
    def test_invalid_or_unexpected_json_passes_through(self, content):
        assert render_json_content(content, "oneshot") == content
        assert render_json_content(content, "classic") == content

    def test_classic_payload_is_not_treated_as_oneshot(self):
        payload = json.dumps({"guess": ["A", "B", "C", "D"]})
        assert render_json_content(payload, "oneshot") == payload


# --- shared end-to-end helpers ------------------------------------------


def _make_puzzle(trap_groups=None):
    return Puzzle(
        id=477, date="2024-09-30", difficulty=3.8,
        words=list(_TEST_WORDS), groups=_make_test_groups(),
        trap_groups=trap_groups,
    )


def _make_game(tmp_path, puzzle, mode="oneshot", structured_output=False):
    with patch.object(ConnectionsGame, '_load_puzzles', return_value=[puzzle]), \
         patch.object(ConnectionsGame, '_load_model_mappings',
                      return_value={"test-model": "test/model"}):
        return ConnectionsGame(_INPUTS, tmp_path, seed=42, mode=mode,
                               structured_output=structured_output)


def _mock_response(content):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    }


class TestPromptTemplateSelection:
    """--structured-output swaps in the `_json` template variant."""

    def test_oneshot_json_template(self, tmp_path):
        game = _make_game(tmp_path, _make_puzzle(), structured_output=True)
        assert '"answer"' in game.prompt_template
        assert "<answer>" not in game.prompt_template
        # Everything the message builder splits on must survive.
        for tag in ("<system>", "<user>", "<puzzle>", "<id>", "<difficulty>"):
            assert tag in game.prompt_template

    def test_classic_json_template(self, tmp_path):
        game = _make_game(tmp_path, _make_puzzle(), mode="classic", structured_output=True)
        assert '"guess"' in game.prompt_template
        assert "<guess>" not in game.prompt_template

    def test_default_still_loads_the_xml_templates(self, tmp_path):
        game = _make_game(tmp_path, _make_puzzle())
        assert "<answer>" in game.prompt_template
        assert game.response_format is None
        assert game.structured_output is False


class TestStructuredEndToEnd:
    """run_evaluation drives the structured one-shot path (mocked adapter)."""

    def test_json_response_scores_a_perfect_oneshot(self, tmp_path):
        puzzle = _make_puzzle()
        game = _make_game(tmp_path, puzzle, structured_output=True)
        content = json.dumps({
            "thinking": "grouped them",
            "answer": [g.words for g in puzzle.groups],
            "traps": [],
            "confidence": 0.95,
        })

        with patch("connections_eval.core.openrouter_adapter.chat",
                   return_value=_mock_response(content)) as chat_mock:
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["structured_output"] is True
        assert summary["puzzles_solved"] == 1
        assert summary["total_score"] == 3
        assert summary["max_score"] == 3
        assert summary["invalid_responses"] == 0
        # The schema reached the adapter.
        rf = chat_mock.call_args.kwargs["response_format"]
        assert rf["json_schema"]["name"] == "connections_oneshot"

    def test_default_run_sends_no_response_format(self, tmp_path):
        puzzle = _make_puzzle()
        game = _make_game(tmp_path, puzzle)
        answer = "<answer>\n" + "\n".join(
            ", ".join(g.words) for g in puzzle.groups) + "\n</answer>"

        with patch("connections_eval.core.openrouter_adapter.chat",
                   return_value=_mock_response(answer)) as chat_mock:
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["structured_output"] is False
        assert "response_format" not in chat_mock.call_args.kwargs

    def test_model_ignoring_the_schema_is_scored_as_today(self, tmp_path):
        """A non-JSON reply under structured output still parses as XML text."""
        puzzle = _make_puzzle()
        game = _make_game(tmp_path, puzzle, structured_output=True)
        answer = "<answer>\n" + "\n".join(
            ", ".join(g.words) for g in puzzle.groups) + "\n</answer>"

        with patch("connections_eval.core.openrouter_adapter.chat",
                   return_value=_mock_response(answer)):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["total_score"] == 3

    def test_json_trap_claim_earns_the_bonus(self, tmp_path):
        puzzle = _make_puzzle(trap_groups=[["FAST", "QUICK", "BRIGHT", "CLEVER"]])
        game = _make_game(tmp_path, puzzle, structured_output=True)
        content = json.dumps({
            "thinking": "spotted the trap",
            "answer": [g.words for g in puzzle.groups],
            "traps": ["FAST", "QUICK", "BRIGHT", "CLEVER"],
            "confidence": 0.9,
        })

        with patch("connections_eval.core.openrouter_adapter.chat",
                   return_value=_mock_response(content)):
            summary = game.run_evaluation("test-model", puzzle_ids=[477])

        assert summary["total_score"] == 5
        assert summary["max_score"] == 5


class TestAdapterPayload:
    """response_format lands in the request body only when supplied."""

    @staticmethod
    def _post_payload(mock_post, **chat_kwargs):
        from connections_eval.adapters import openrouter_adapter

        mock_post.return_value = MagicMock(
            status_code=200, ok=True,
            json=lambda: {"choices": [{"message": {"content": "hi"},
                                       "finish_reason": "stop"}]},
        )
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            openrouter_adapter.chat([{"role": "user", "content": "hi"}],
                                    "test/model", **chat_kwargs)
        return mock_post.call_args.kwargs["json"]

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    def test_payload_carries_response_format(self, mock_post):
        rf = build_response_format("oneshot")
        payload = self._post_payload(mock_post, response_format=rf)
        assert payload["response_format"] == rf

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    def test_payload_omits_response_format_by_default(self, mock_post):
        assert "response_format" not in self._post_payload(mock_post)
