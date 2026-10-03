"""
Recording and replaying model responses, including the guarantee
that a replayed answer belongs to the request that asked for it.
"""

import json

import pytest

from app.evals.cassettes import (
    Cassette,
    LiveGateway,
    MissingRecording,
    ReplayGateway,
    request_key,
)


# ============================================================
# KEYS
# ============================================================

def test_the_same_request_gets_the_same_key():

    assert request_key("answer", "q", "c") == request_key("answer", "q", "c")


def test_a_different_request_gets_a_different_key():

    assert request_key("answer", "q", "c") != request_key("answer", "q", "other")
    assert request_key("answer", "q", "c") != request_key("retrieval", "q", "c")


def test_the_key_is_not_fooled_by_where_the_split_falls():
    """Parts are separated, so "ab"+"c" and "a"+"bc" differ."""

    assert request_key("answer", "ab", "c") != request_key("answer", "a", "bc")


def test_the_key_names_its_kind():

    assert request_key("retrieval", "q", 3).startswith("retrieval:")


# ============================================================
# CASSETTE FILE
# ============================================================

def test_a_missing_file_loads_as_empty(tmp_path):

    cassette = Cassette.load(tmp_path / "absent.json")

    assert cassette.entries == {}
    assert cassette.dirty is False


def test_saving_and_loading_round_trips(tmp_path):

    path = tmp_path / "cassette.json"

    cassette = Cassette.load(path)
    cassette.put(request_key("answer", "q", "c"), "an answer", summary="q")

    assert cassette.dirty is True

    cassette.save()

    assert cassette.dirty is False

    reloaded = Cassette.load(path)

    assert reloaded.get(request_key("answer", "q", "c")) == "an answer"


def test_the_saved_file_is_readable_json(tmp_path):

    path = tmp_path / "cassette.json"

    cassette = Cassette.load(path)
    cassette.put(request_key("retrieval", "q", 3), ["chunk"], summary="q")
    cassette.save()

    payload = json.loads(path.read_text(encoding="utf-8"))

    entry = next(iter(payload["entries"].values()))

    assert entry["summary"] == "q"
    assert entry["response"] == ["chunk"]
    assert "--live --record" in payload["note"]


def test_saving_creates_the_folder(tmp_path):

    path = tmp_path / "nested" / "deeper" / "cassette.json"

    cassette = Cassette.load(path)
    cassette.put("answer:x", "value")
    cassette.save()

    assert path.is_file()


def test_a_missing_recording_says_how_to_record(tmp_path):

    cassette = Cassette.load(tmp_path / "cassette.json")

    with pytest.raises(MissingRecording, match="--live --record"):
        cassette.get("answer:missing")


# ============================================================
# GATEWAYS
# ============================================================

def test_replay_returns_the_recorded_response(tmp_path):

    cassette = Cassette.load(tmp_path / "cassette.json")
    cassette.put(request_key("answer", "q", "c"), "recorded")
    cassette.put(request_key("retrieval", "q", 3), ["chunk"])

    gateway = ReplayGateway(cassette)

    assert gateway.answer("q", "c") == "recorded"
    assert gateway.retrieve("q", 3) == ["chunk"]


def test_replay_will_not_reuse_an_answer_for_another_context(tmp_path):

    cassette = Cassette.load(tmp_path / "cassette.json")
    cassette.put(request_key("answer", "q", "original context"), "recorded")

    with pytest.raises(MissingRecording):
        ReplayGateway(cassette).answer("q", "a different context")


def test_live_calls_the_service_and_records(tmp_path, monkeypatch):

    from app.services import llm_service

    monkeypatch.setattr(
        llm_service,
        "generate_answer",
        lambda question, context: f"answer to {question}",
    )

    cassette = Cassette.load(tmp_path / "cassette.json")

    assert LiveGateway(cassette).answer("q", "c") == "answer to q"

    assert ReplayGateway(cassette).answer("q", "c") == "answer to q"


def test_live_records_retrieval_as_a_list(tmp_path, monkeypatch):

    from app.services import rag_service

    monkeypatch.setattr(
        rag_service, "retrieve_context", lambda query, top_k: iter(["a", "b"])
    )

    cassette = Cassette.load(tmp_path / "cassette.json")

    assert LiveGateway(cassette).retrieve("q", 2) == ["a", "b"]

    # Stored as a list, so it survives the round trip through JSON.
    assert ReplayGateway(cassette).retrieve("q", 2) == ["a", "b"]


def test_live_without_a_cassette_records_nothing(tmp_path, monkeypatch):

    from app.services import llm_service

    monkeypatch.setattr(
        llm_service, "generate_answer", lambda question, context: "answer"
    )

    assert LiveGateway(None).answer("q", "c") == "answer"
