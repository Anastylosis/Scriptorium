import pytest

from stash_subs import config


def test_empty_environment_yields_working_defaults():
    cfg = config.from_env({})
    assert cfg.stash.url == "http://stash:9999"
    assert cfg.model.name == "large-v3-turbo"
    assert cfg.run.poll_seconds == 120
    assert cfg.tags.request == ["subs:en", "subs:es", "subs:auto"]


@pytest.mark.parametrize("name,path,value", [
    ("STASH_URL", ("stash", "url"), "http://box:9999"),
    ("STASH_API_KEY", ("stash", "api_key"), "secret"),
    ("PATH_FROM", ("stash", "path_from"), "/a"),
    ("PATH_TO", ("stash", "path_to"), "/b"),
    ("MODEL", ("model", "name"), "large-v3"),
    ("MODEL_DIR", ("model", "directory"), "/m"),
    ("DEVICE", ("model", "device"), "cuda"),
    ("COMPUTE_TYPE", ("model", "compute_type"), "float16"),
    ("TRANSLATE_MODEL", ("model", "translate_model"), "large-v3"),
    ("DONE_TAG", ("tags", "done"), "done"),
    ("FAILED_TAG", ("tags", "failed"), "failed"),
    ("OLLAMA_MODEL", ("ollama", "model"), "qwen3:8b"),
    ("TRANSLATE_MODE", ("ollama", "mode"), "json"),
    ("HTTP_HOST", ("server", "host"), "127.0.0.1"),
])
def test_every_string_env_var_is_wired(name, path, value):
    cfg = config.from_env({name: value})
    section, field = path
    assert getattr(getattr(cfg, section), field) == value


@pytest.mark.parametrize("name,path,value", [
    ("THREADS", ("model", "threads"), 12),
    ("BEAM_SIZE", ("model", "beam_size"), 1),
    ("OLLAMA_BATCH", ("ollama", "batch"), 40),
    ("POLL_SECONDS", ("run", "poll_seconds"), 30),
    ("HTTP_PORT", ("server", "port"), 9000),
])
def test_every_int_env_var_is_wired(name, path, value):
    cfg = config.from_env({name: str(value)})
    section, field = path
    assert getattr(getattr(cfg, section), field) == value


@pytest.mark.parametrize("name,path", [
    ("RUN_ONCE", ("run", "run_once")),
    ("DRY_RUN", ("run", "dry_run")),
    ("OLLAMA_PULL", ("ollama", "pull")),
])
def test_every_bool_env_var_is_wired(name, path):
    section, field = path
    assert getattr(getattr(config.from_env({name: "1"}), section), field) is True
    assert getattr(getattr(config.from_env({name: "0"}), section), field) is False


def test_empty_string_is_treated_as_unset():
    # The shipped compose writes STASH_API_KEY= when auth is off, and
    # int("") would otherwise crash at startup.
    cfg = config.from_env({"STASH_API_KEY": "", "POLL_SECONDS": "", "STASH_URL": ""})
    assert cfg.stash.api_key == ""
    assert cfg.run.poll_seconds == 120
    assert cfg.stash.url == "http://stash:9999"


def test_trailing_slash_is_stripped_from_urls():
    cfg = config.from_env({"STASH_URL": "http://box:9999/",
                           "OLLAMA_URL": "http://ollama:11434/"})
    assert cfg.stash.url == "http://box:9999"
    assert cfg.ollama.url == "http://ollama:11434"


def test_request_tags_are_split_and_trimmed():
    cfg = config.from_env({"REQUEST_TAGS": " subs:en , subs:fr ,, "})
    assert cfg.tags.request == ["subs:en", "subs:fr"]


def test_a_non_numeric_int_is_a_clear_error():
    with pytest.raises(config.ConfigError, match="THREADS"):
        config.from_env({"THREADS": "lots"})
