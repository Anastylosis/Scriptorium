import pytest

from scriptorium import config, status
from scriptorium.asr import whisper_translates
from scriptorium.translate import resolve_mode


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
