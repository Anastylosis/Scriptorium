import json

import pytest

from scriptorium import config
from scriptorium.translate import Ollama, TranslateError

CUES = [(0.0, 1.0, "one"), (1.0, 2.0, "two"), (2.0, 3.0, "three")]


def make(replies, **env):
    """An Ollama whose chat() replays `replies`, one per call."""
    env.setdefault("OLLAMA_URL", "http://ollama:11434")
    client = Ollama(config.from_env(env).ollama)
    calls = []

    def chat(prompt, json_format=False):
        calls.append(prompt)
        r = replies[len(calls) - 1] if len(calls) <= len(replies) else replies[-1]
        if callable(r):
            return r(prompt)
        return r

    client.chat = chat
    return client, calls


def test_requires_ollama_url():
    client = Ollama(config.from_env({}).ollama)
    with pytest.raises(TranslateError, match="OLLAMA_URL"):
        client.translate(CUES, "en", "es")


def test_lines_mode_aligns_and_preserves_timings():
    client, _ = make(["1. uno\n2. dos\n3. tres"], TRANSLATE_MODE="lines")
    out = client.translate(CUES, "en", "es")
    assert out == [(0.0, 1.0, "uno"), (1.0, 2.0, "dos"), (2.0, 3.0, "tres")]


def test_lines_mode_strips_both_numbering_styles():
    client, _ = make(["1) uno\n2) dos\n3) tres"], TRANSLATE_MODE="lines")
    assert [c[2] for c in client.translate(CUES, "en", "es")] == ["uno", "dos", "tres"]


def test_line_count_mismatch_falls_back_to_one_request_per_line():
    # Losing alignment would silently shift every subsequent subtitle, so a
    # short reply must trigger a per-line retry rather than being zipped.
    client, calls = make(
        ["1. uno\n2. dos", "uno", "dos", "tres"], TRANSLATE_MODE="lines")
    out = client.translate(CUES, "en", "es")
    assert [c[2] for c in out] == ["uno", "dos", "tres"]
    assert len(calls) == 4, "expected one batch call plus three per-line calls"
    assert [c[:2] for c in out] == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]


def test_per_line_fallback_keeps_source_text_when_a_line_fails():
    def boom(prompt):
        raise RuntimeError("model died")

    client, _ = make(["1. uno", "uno", boom, "tres"], TRANSLATE_MODE="lines")
    out = client.translate(CUES, "en", "es")
    assert [c[2] for c in out] == ["uno", "two", "tres"]


def test_json_mode_maps_by_key():
    client, _ = make([json.dumps({"0": "uno", "1": "dos", "2": "tres"})],
                     TRANSLATE_MODE="json")
    assert [c[2] for c in client.translate(CUES, "en", "es")] == ["uno", "dos", "tres"]


def test_json_mode_keeps_source_text_for_a_missing_key():
    client, _ = make([json.dumps({"0": "uno", "2": "tres"})], TRANSLATE_MODE="json")
    assert [c[2] for c in client.translate(CUES, "en", "es")] == ["uno", "two", "tres"]


def test_json_mode_survives_malformed_json():
    client, _ = make(["not json at all"], TRANSLATE_MODE="json")
    assert [c[2] for c in client.translate(CUES, "en", "es")] == ["one", "two", "three"]


def test_batches_are_chunked():
    client, calls = make(
        [json.dumps({"0": "uno", "1": "dos"}), json.dumps({"0": "tres"})],
        TRANSLATE_MODE="json", OLLAMA_BATCH="2")
    out = client.translate(CUES, "en", "es")
    assert len(calls) == 2
    assert [c[2] for c in out] == ["uno", "dos", "tres"]


def test_progress_is_reported_per_batch():
    seen = []
    client, _ = make(
        [json.dumps({"0": "uno", "1": "dos"}), json.dumps({"0": "tres"})],
        TRANSLATE_MODE="json", OLLAMA_BATCH="2")
    client.translate(CUES, "en", "es", on_progress=lambda d, t: seen.append((d, t)))
    assert seen == [(2, 3), (3, 3)]


def test_language_names_reach_the_prompt():
    client, calls = make(["1. uno\n2. dos\n3. tres"], TRANSLATE_MODE="lines")
    client.translate(CUES, "en", "es")
    assert "English" in calls[0] and "Spanish" in calls[0]


def test_unknown_language_code_falls_back_to_the_code():
    client, calls = make(["1. a\n2. b\n3. c"], TRANSLATE_MODE="lines")
    client.translate(CUES, "en", "qq")
    assert "qq" in calls[0]
