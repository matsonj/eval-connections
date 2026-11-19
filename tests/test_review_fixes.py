"""Regression tests for review fixes on the efficiency cleanup (#29)."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from connections_eval.utils.retry import _retry_after_seconds, retry_after_from_headers

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generate_logs_view.py"


@pytest.fixture(scope="module")
def logs_view():
    spec = importlib.util.spec_from_file_location("generate_logs_view", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- Retry-After header lookup -------------------------------------------

@pytest.mark.parametrize("headers", [
    {"Retry-After": "30"}, {"retry-after": "30"}, {"RETRY-AFTER": "30"},
])
def test_retry_after_any_case_plain_dict(headers):
    assert retry_after_from_headers(headers) == 30.0
    exc = SimpleNamespace(response=SimpleNamespace(headers=headers))
    assert _retry_after_seconds(exc) == 30.0


@pytest.mark.parametrize("headers", [None, {}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}])
def test_retry_after_absent_or_date_is_none(headers):
    assert retry_after_from_headers(headers) is None


def test_adapter_429_passes_lowercase_retry_after_to_limiter():
    from connections_eval.adapters import openrouter_adapter as oa
    resp = MagicMock(status_code=429, headers={"retry-after": "7"})
    resp.raise_for_status.side_effect = oa.requests.HTTPError(response=resp)
    limiter = MagicMock()
    with patch.object(oa, "get_rate_limiter", return_value=limiter), \
         patch.object(oa.requests, "post", return_value=resp), \
         patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}), \
         patch("connections_eval.utils.retry.time.sleep"):
        with pytest.raises(Exception):
            oa.chat([{"role": "user", "content": "hi"}], "google/gemini-4-carbon")
    assert limiter.on_429.call_args.kwargs["retry_after"] == 7.0


# --- Log page thinking split ---------------------------------------------

def test_unclosed_thinking_keeps_guess_visible(logs_view):
    thinking, rest = logs_view.split_thinking_blocks(
        "<thinking>these four are fruits <guess>APPLE, PEAR, PLUM, FIG</guess>")
    assert thinking == "these four are fruits"
    assert "<guess>APPLE, PEAR, PLUM, FIG</guess>" in rest


def test_closed_thinking_unchanged(logs_view):
    thinking, rest = logs_view.split_thinking_blocks(
        "<thinking>reasoning</thinking>\n<guess>A, B, C, D</guess>")
    assert thinking == "reasoning"
    assert rest == "<guess>A, B, C, D</guess>"


def test_unclosed_thinking_without_guess_stays_thinking(logs_view):
    thinking, rest = logs_view.split_thinking_blocks("<thinking>cut off mid-thought")
    assert thinking == "cut off mid-thought"
    assert rest == ""


# --- Index regenerated with no rows --------------------------------------

def test_main_writes_index_when_no_rows(logs_view, tmp_path, monkeypatch):
    monkeypatch.setattr(logs_view, "DOCS_LOG_DIR", tmp_path)
    monkeypatch.setattr(logs_view, "read_allowed_run_ids", lambda: [])
    stale = tmp_path / "index.html"
    stale.write_text("STALE previous publish")
    monkeypatch.setattr(sys, "argv", ["generate_logs_view.py"])
    logs_view.main()
    assert "STALE" not in stale.read_text()
