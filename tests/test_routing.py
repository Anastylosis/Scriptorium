import pytest

from stash_subs import config, status
from stash_subs.asr import whisper_translates
from stash_subs.translate import resolve_mode
from stash_subs.worker import Worker


def worker(env=None):
    cfg = config.from_env(env or {})
    return Worker(cfg, status.Store(), client=object())


@pytest.mark.parametrize("model,translates", [
    ("large-v3", True),
    ("medium", True),
    ("distil-large-v3", True),
    ("large-v3-turbo", False),
    ("LARGE-V3-TURBO", False),
    ("turbo", False),
    ("deepdml/faster-whisper-large-v3-turbo-ct2", False),
])
def test_turbo_checkpoints_are_known_not_to_translate(model, translates):
    # Turbo was fine-tuned with translation data excluded and returns
    # source-language text for task="translate" without erroring, so this
    # guard is what stops Spanish audio landing in a file named .en.srt.
    assert whisper_translates(model) is translates


def test_targets_for_extracts_language_from_request_tags():
    scene = {"tags": [{"id": "1", "name": "subs:en"},
                      {"id": "2", "name": "subs:es"},
                      {"id": "9", "name": "favourite"}]}
    assert sorted(worker().targets_for(scene)) == ["en", "es"]


def test_targets_for_is_case_insensitive():
    scene = {"tags": [{"id": "1", "name": "SUBS:EN"}]}
    assert worker().targets_for(scene) == ["en"]


def test_targets_for_ignores_unrelated_tags():
    scene = {"tags": [{"id": "1", "name": "subs:done"},
                      {"id": "2", "name": "4k"}]}
    assert worker().targets_for(scene) == []


def test_targets_for_handles_a_scene_with_no_tags():
    assert worker().targets_for({"tags": []}) == []


def test_targets_for_honours_a_custom_request_tag_list():
    w = worker({"REQUEST_TAGS": "subs:fr"})
    scene = {"tags": [{"id": "1", "name": "subs:fr"}, {"id": "2", "name": "subs:en"}]}
    assert w.targets_for(scene) == ["fr"]


@pytest.mark.parametrize("model,mode", [
    ("translategemma:4b", "lines"),
    ("translategemma:12b", "lines"),
    ("opus-mt-en-es", "lines"),
    ("madlad400", "lines"),
    ("some-translator", "lines"),
    ("qwen3:8b", "json"),
    ("llama3", "json"),
])
def test_translate_mode_autodetects_from_model_name(model, mode):
    # Dedicated translation models will not emit structured JSON.
    cfg = config.from_env({"OLLAMA_MODEL": model}).ollama
    assert resolve_mode(cfg) == mode


def test_translate_mode_respects_an_explicit_override():
    cfg = config.from_env({"OLLAMA_MODEL": "translategemma:4b",
                           "TRANSLATE_MODE": "json"}).ollama
    assert resolve_mode(cfg) == "json"


@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00"),
    (59, "0:59"),
    (60, "1:00"),
    (599, "9:59"),
    (3600, "1:00:00"),
    (3661, "1:01:01"),
])
def test_fmt_hms(seconds, expected):
    assert status.fmt_hms(seconds) == expected
