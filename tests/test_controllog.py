import json
import threading

import pytest

import controllog as cl
from controllog.sdk import post


EVENT_KEYS = {
    "event_id", "event_time", "ingest_time", "kind", "actor_agent_id",
    "actor_task_id", "project_id", "run_id", "source", "idempotency_key",
    "payload_json",
}
POSTING_KEYS = {
    "posting_id", "event_id", "account_type", "account_id", "unit",
    "delta_numeric", "dims_json",
}


@pytest.fixture
def log_dir(tmp_path):
    cl.init(project_id="test_project", log_dir=tmp_path)
    return tmp_path


def _read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_event_and_posting_key_sets(log_dir):
    postings = [
        post("resource.tokens", "provider:x", "+tokens", -5, {"model": "m"}),
        post("resource.tokens", "project:test_project", "+tokens", 5, {"model": "m"}),
    ]
    cl.event(kind="raw_event", postings=postings, project_id="test_project")

    date_dirs = list((log_dir / "controllog").iterdir())
    assert len(date_dirs) == 1
    events = _read_lines(date_dirs[0] / "events.jsonl")
    postings_out = _read_lines(date_dirs[0] / "postings.jsonl")

    assert len(events) == 1
    assert set(events[0].keys()) == EVENT_KEYS
    assert len(postings_out) == 2
    for p in postings_out:
        assert set(p.keys()) == POSTING_KEYS
    assert events[0]["event_time"] == events[0]["ingest_time"]


def test_unbalanced_postings_raise(log_dir):
    postings = [post("resource.tokens", "provider:x", "+tokens", -5, {})]
    with pytest.raises(ValueError):
        cl.event(kind="bad_event", postings=postings, project_id="test_project")


def test_concurrent_events_all_persisted(log_dir):
    def emit(i):
        cl.event(kind="concurrent_event", payload={"i": i}, project_id="test_project")

    threads = [threading.Thread(target=emit, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    date_dirs = list((log_dir / "controllog").iterdir())
    events = _read_lines(date_dirs[0] / "events.jsonl")
    assert len(events) == 8
    assert all(set(e.keys()) == EVENT_KEYS for e in events)
