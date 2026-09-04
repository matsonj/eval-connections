"""Tests for opt-in structured (JSON schema) output."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from connections_eval.core import ConnectionsGame, Puzzle, PuzzleGroup
from connections_eval.structured import build_response_format, json_prompt_template, render_json_content

INPUTS = Path(__file__).parent.parent / "inputs"
ANSWER = [["APPLE", "BANANA", "CHERRY", "GRAPE"], ["BLUE", "GREEN", "RED", "YELLOW"],
          ["FAST", "QUICK", "RAPID", "SWIFT"], ["BRIGHT", "CLEVER", "SMART", "WISE"]]
TAIL = f'"answer": {json.dumps(ANSWER)}, "traps": ["FAST", "QUICK", "BRIGHT", "CLEVER"], "confidence": 0.65}}'


def _puzzle():
    groups = [PuzzleGroup("Fruits", "green", ANSWER[0]), PuzzleGroup("Colors", "yellow", ANSWER[1]),
              PuzzleGroup("Speed", "blue", ANSWER[2]), PuzzleGroup("Smart", "purple", ANSWER[3])]
    return Puzzle(id=477, date="2024-09-30", difficulty=3.8, words=[w for g in ANSWER for w in g],
                  groups=groups, canonical=True, trap_groups=[["FAST", "QUICK", "BRIGHT", "CLEVER"]])


def _response(content):
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20}}


class TestSchema:
    def test_envelope_and_shape(self):
        rf = build_response_format("oneshot")
        assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
        schema = rf["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"thinking", "answer", "traps", "confidence"}
        ans = schema["properties"]["answer"]
        assert (ans["minItems"], ans["maxItems"]) == (4, 4)
        assert (ans["items"]["minItems"], ans["items"]["maxItems"]) == (4, 4)
        assert build_response_format("classic")["json_schema"]["schema"]["properties"]["guess"]["maxItems"] == 4

    def test_no_provider_hostile_keywords(self):
        blob = json.dumps(build_response_format("oneshot")) + json.dumps(build_response_format("classic"))
        for kw in ("enum", "anyOf", "oneOf", "pattern", "$ref"):
            assert kw not in blob


class TestPromptTemplate:
    @pytest.mark.parametrize("filename, mode, key", [
        ("prompt_template_oneshot.xml", "oneshot", '"traps"'),
        ("prompt_template.xml", "classic", '"guess"'),
    ])
    def test_json_section_replaces_xml_format(self, filename, mode, key):
        xml = (INPUTS / filename).read_text()
        derived = json_prompt_template(xml, mode)
        assert derived.startswith(xml.split("RESPONSE FORMAT:")[0])
        assert key in derived and "<answer>" not in derived and "<guess>" not in derived
        assert derived.rstrip().endswith(xml.split("</user>")[1].rstrip())


class TestRendering:
    def test_full_object(self):
        out = render_json_content('{"thinking": " why ", ' + TAIL, "oneshot")
        assert "<thinking>why</thinking>" in out
        assert "<answer>\nAPPLE, BANANA, CHERRY, GRAPE\n" in out and out.count("\n") >= 6
        assert "<traps>\nFAST, QUICK, BRIGHT, CLEVER\n</traps>" in out
        assert "<confidence>\n0.65\n</confidence>" in out

    def test_empty_traps_is_na_and_words_are_normalized(self):
        out = render_json_content('{"answer": [[" apple ", "b", "c", "d"]], "traps": []}', "oneshot")
        assert "APPLE, B, C, D" in out and "<traps>\nN/A\n</traps>" in out and "<confidence>" not in out

    def test_classic(self):
        out = render_json_content('{"guess": ["a", "b", "c", "d"], "confidence": 0.4}', "classic")
        assert "<guess>\nA, B, C, D\n</guess>" in out

    @pytest.mark.parametrize("content", ["plain text", "[1, 2]", '{"thinking": "no key"}', "", '{"thinking": "trunc'])
    def test_non_json_or_wrong_shape_passes_through(self, content):
        assert render_json_content(content, "oneshot") == content

    def test_tolerates_fence_newlines_bad_escapes_and_wrapper(self):
        cases = [
            "```json\n{" + TAIL + "\n```",
            '{"thinking": "line one\nline two\ttabbed", ' + TAIL,
            r'{"thinking": "SKY \d GREEK\ROMAN", ' + TAIL,
            "[{" + TAIL + "]",
        ]
        for content in cases:
            assert "BRIGHT, CLEVER, SMART, WISE" in render_json_content(content, "oneshot"), content

    def test_salvages_arrays_from_unparseable_json(self):
        content = '{"thinking": "he said "hi" and \\q left", ' + TAIL  # unrecoverable object
        out = render_json_content(content, "oneshot")
        assert "<answer>" in out and "<traps>" in out and "0.65" in out and "<thinking>" not in out
        assert "<guess>\nA, B, C, D\n</guess>" in render_json_content(
            '{"thinking": "bad "q", "guess": ["a","b","c","d"]}', "classic")


class TestEndToEnd:
    def _game(self, tmp_path, **kw):
        game = ConnectionsGame(INPUTS, tmp_path, seed=1, mode="oneshot", **kw)
        game.puzzles = [_puzzle()]
        game.MODEL_CONFIG = {"test-model": "test/model"}
        return game

    def test_json_response_scores_perfectly_and_sends_schema(self, tmp_path):
        game = self._game(tmp_path, structured_output=True)
        content = json.dumps({"thinking": "x", "answer": ANSWER, "traps": ANSWER[2][:2] + ANSWER[3][:2], "confidence": 0.9})
        with patch("connections_eval.core.openrouter_adapter.chat", return_value=_response(content)) as chat:
            summary = game.run_evaluation("test-model", puzzle_ids=[477])
        assert summary["total_score"] == 5 and summary["structured_output"] is True
        assert chat.call_args.kwargs["response_format"] == build_response_format("oneshot")
        assert "JSON object" in game.prompt_template

    def test_default_run_is_unchanged(self, tmp_path):
        game = self._game(tmp_path)
        xml = "<answer>\n" + "\n".join(", ".join(g) for g in ANSWER) + "\n</answer>\n<traps>\nN/A\n</traps>"
        with patch("connections_eval.core.openrouter_adapter.chat", return_value=_response(xml)) as chat:
            summary = game.run_evaluation("test-model", puzzle_ids=[477])
        assert summary["total_score"] == 3 and summary["structured_output"] is False
        assert "response_format" not in chat.call_args.kwargs
        assert "<answer>" in game.prompt_template


class TestAdapterPayload:
    @staticmethod
    def _payload(mock_post, model, **kw):
        from connections_eval.adapters import openrouter_adapter
        mock_post.return_value = MagicMock(status_code=200, ok=True, json=lambda: _response("hi"))
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}):
            openrouter_adapter.chat([{"role": "user", "content": "hi"}], model, **kw)
        return mock_post.call_args.kwargs["json"]

    @patch("connections_eval.adapters.openrouter_adapter.requests.post")
    def test_response_format_and_cap_only_when_requested(self, mock_post):
        from connections_eval.adapters import openrouter_adapter
        rf = build_response_format("oneshot")
        with_rf = self._payload(mock_post, "any/thinking-model", thinking=True, response_format=rf)
        assert with_rf["response_format"] == rf
        assert with_rf["max_tokens"] == openrouter_adapter.STRUCTURED_OUTPUT_MAX_TOKENS
        plain = self._payload(mock_post, "any/thinking-model", thinking=True)
        assert "response_format" not in plain and "max_tokens" not in plain
        assert self._payload(mock_post, "test/model", response_format=rf)["max_tokens"] == 25000
