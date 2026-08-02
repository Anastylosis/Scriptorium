import json

import pytest

import stash_subs as s

CUES = [(0.0, 1.0, "one"), (1.0, 2.0, "two"), (2.0, 3.0, "three")]


@pytest.fixture(autouse=True)
def _ollama_configured(monkeypatch):
    monkeypatch.setattr(s, "OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setattr(s, "OLLAMA_BATCH", 20)
    monkeypatch.setattr(s, "STATE", dict(s.STATE, duration=100.0))


def fake_chat(monkeypatch, replies):
    """Install a _chat stub; `replies` is a list consumed one call at a time."""
    calls = []

    def _chat(prompt, json_format):
        calls.append(prompt)
        r = replies[len(calls) - 1] if len(calls) <= len(replies) else replies[-1]
        if callable(r):
            return r(prompt)
        return r

    monkeypatch.setattr(s, "_chat", _chat)
    return calls


def test_requires_ollama_url(monkeypatch):
    monkeypatch.setattr(s, "OLLAMA_URL", "")
    with pytest.raises(RuntimeError, match="OLLAMA_URL"):
        s.ollama_translate(CUES, "en", "es")


def test_lines_mode_aligns_and_preserves_timings(monkeypatch):
    monkeypatch.setattr(s, "TRANSLATE_MODE", "lines")
    fake_chat(monkeypatch, ["1. uno\n2. dos\n3. tres"])
    out = s.ollama_translate(CUES, "en", "es")
    assert out == [(0.0, 1.0, "uno"), (1.0, 2.0, "dos"), (2.0, 3.0, "tres")]


def test_lines_mode_strips_both_numbering_styles(monkeypatch):
    monkeypatch.setattr(s, "TRANSLATE_MODE", "lines")
    fake_chat(monkeypatch, ["1) uno\n2) dos\n3) tres"])
    out = s.ollama_translate(CUES, "en", "es")
    assert [c[2] for c in out] == ["uno", "dos", "tres"]


def test_line_count_mismatch_falls_back_to_one_request_per_line(monkeypatch):
    # Losing alignment would silently shift every subsequent subtitle, so a
    # short reply must trigger a per-line retry rather than being zipped.
    monkeypatch.setattr(s, "TRANSLATE_MODE", "lines")
    calls = fake_chat(monkeypatch, [
        "1. uno\n2. dos",          # only two lines for three cues
        "uno", "dos", "tres",      # the per-line retries
    ])
    out = s.ollama_translate(CUES, "en", "es")
    assert [c[2] for c in out] == ["uno", "dos", "tres"]
    assert len(calls) == 4, "expected one batch call plus three per-line calls"
    assert [c[:2] for c in out] == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]


def test_per_line_fallback_keeps_source_text_when_a_line_fails(monkeypatch):
    monkeypatch.setattr(s, "TRANSLATE_MODE", "lines")

    def boom(prompt):
        raise RuntimeError("model died")

    fake_chat(monkeypatch, ["1. uno", "uno", boom, "tres"])
    out = s.ollama_translate(CUES, "en", "es")
    assert [c[2] for c in out] == ["uno", "two", "tres"]


def test_json_mode_maps_by_key(monkeypatch):
    monkeypatch.setattr(s, "TRANSLATE_MODE", "json")
    fake_chat(monkeypatch, [json.dumps({"0": "uno", "1": "dos", "2": "tres"})])
    out = s.ollama_translate(CUES, "en", "es")
    assert [c[2] for c in out] == ["uno", "dos", "tres"]


def test_json_mode_keeps_source_text_for_a_missing_key(monkeypatch):
    monkeypatch.setattr(s, "TRANSLATE_MODE", "json")
    fake_chat(monkeypatch, [json.dumps({"0": "uno", "2": "tres"})])
    out = s.ollama_translate(CUES, "en", "es")
    assert [c[2] for c in out] == ["uno", "two", "tres"]


def test_json_mode_survives_malformed_json(monkeypatch):
    monkeypatch.setattr(s, "TRANSLATE_MODE", "json")
    fake_chat(monkeypatch, ["not json at all"])
    out = s.ollama_translate(CUES, "en", "es")
    assert [c[2] for c in out] == ["one", "two", "three"]


def test_batches_are_chunked(monkeypatch):
    monkeypatch.setattr(s, "TRANSLATE_MODE", "json")
    monkeypatch.setattr(s, "OLLAMA_BATCH", 2)
    calls = fake_chat(monkeypatch, [
        json.dumps({"0": "uno", "1": "dos"}),
        json.dumps({"0": "tres"}),
    ])
    out = s.ollama_translate(CUES, "en", "es")
    assert len(calls) == 2
    assert [c[2] for c in out] == ["uno", "dos", "tres"]


def test_language_names_reach_the_prompt(monkeypatch):
    monkeypatch.setattr(s, "TRANSLATE_MODE", "lines")
    calls = fake_chat(monkeypatch, ["1. uno\n2. dos\n3. tres"])
    s.ollama_translate(CUES, "en", "es")
    assert "English" in calls[0] and "Spanish" in calls[0]


def test_unknown_language_code_falls_back_to_the_code(monkeypatch):
    monkeypatch.setattr(s, "TRANSLATE_MODE", "lines")
    calls = fake_chat(monkeypatch, ["1. a\n2. b\n3. c"])
    s.ollama_translate(CUES, "en", "qq")
    assert "qq" in calls[0]
