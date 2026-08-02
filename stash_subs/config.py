"""Configuration, read from the environment.

Every setting has a default, so an empty environment produces a usable
config. An environment variable set to the empty string is treated as
unset: the shipped compose file writes `STASH_API_KEY=` when auth is off,
and `int("")` would otherwise crash at startup.
"""

import os
from dataclasses import dataclass, field
from typing import Mapping


class ConfigError(ValueError):
    pass


def _get(env, name, default):
    v = env.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _int(env, name, default):
    raw = _get(env, name, None)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from None


def _bool(env, name, default):
    raw = _get(env, name, None)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _list(env, name, default):
    raw = _get(env, name, None)
    if raw is None:
        return list(default)
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass(frozen=True)
class StashCfg:
    url: str = "http://stash:9999"
    api_key: str = ""
    # Stash reports paths as its own process sees them; map onto ours.
    path_from: str = "/data"
    path_to: str = "/data"


@dataclass(frozen=True)
class ModelCfg:
    name: str = "large-v3-turbo"
    directory: str = "/models"
    device: str = "cpu"
    compute_type: str = "int8"
    threads: int = 6
    beam_size: int = 5
    # A full model used for speech->English when `name` is a turbo
    # checkpoint, which cannot translate.
    translate_model: str = ""


@dataclass(frozen=True)
class TagsCfg:
    request: list = field(default_factory=lambda: ["subs:en", "subs:es", "subs:auto"])
    # True when REQUEST_TAGS was actually set, so a user-narrowed list can be
    # told apart from the built-in default.
    request_explicit: bool = False
    # auto: discover unless REQUEST_TAGS narrows the list deliberately.
    discover: str = "auto"
    # Made at startup. Other languages are created by the user on demand.
    create: list = field(default_factory=lambda: ["subs:en"])
    ignore: list = field(default_factory=list)
    done: str = "subs:done"
    failed: str = "subs:failed"


@dataclass(frozen=True)
class OllamaCfg:
    url: str = ""
    model: str = "translategemma:4b"
    batch: int = 20
    pull: bool = True
    mode: str = "auto"


@dataclass(frozen=True)
class RunCfg:
    poll_seconds: int = 120
    run_once: bool = False
    dry_run: bool = False
    overwrite: bool = False


@dataclass(frozen=True)
class ServerCfg:
    host: str = "0.0.0.0"
    port: int = 8088


@dataclass(frozen=True)
class Config:
    stash: StashCfg = field(default_factory=StashCfg)
    model: ModelCfg = field(default_factory=ModelCfg)
    tags: TagsCfg = field(default_factory=TagsCfg)
    ollama: OllamaCfg = field(default_factory=OllamaCfg)
    run: RunCfg = field(default_factory=RunCfg)
    server: ServerCfg = field(default_factory=ServerCfg)


def from_env(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    return Config(
        stash=StashCfg(
            url=_get(env, "STASH_URL", "http://stash:9999").rstrip("/"),
            api_key=_get(env, "STASH_API_KEY", ""),
            path_from=_get(env, "PATH_FROM", "/data"),
            path_to=_get(env, "PATH_TO", "/data"),
        ),
        model=ModelCfg(
            name=_get(env, "MODEL", "large-v3-turbo"),
            directory=_get(env, "MODEL_DIR", "/models"),
            device=_get(env, "DEVICE", "cpu"),
            compute_type=_get(env, "COMPUTE_TYPE", "int8"),
            threads=_int(env, "THREADS", 6),
            beam_size=_int(env, "BEAM_SIZE", 5),
            translate_model=_get(env, "TRANSLATE_MODEL", ""),
        ),
        tags=TagsCfg(
            request=_list(env, "REQUEST_TAGS", ["subs:en", "subs:es", "subs:auto"]),
            request_explicit=_get(env, "REQUEST_TAGS", None) is not None,
            discover=_get(env, "TAG_DISCOVERY", "auto").lower(),
            create=_list(env, "CREATE_TAGS", ["subs:en"]),
            ignore=_list(env, "IGNORE_TAGS", []),
            done=_get(env, "DONE_TAG", "subs:done"),
            failed=_get(env, "FAILED_TAG", "subs:failed"),
        ),
        ollama=OllamaCfg(
            url=_get(env, "OLLAMA_URL", "").rstrip("/"),
            model=_get(env, "OLLAMA_MODEL", "translategemma:4b"),
            batch=_int(env, "OLLAMA_BATCH", 20),
            pull=_bool(env, "OLLAMA_PULL", True),
            mode=_get(env, "TRANSLATE_MODE", "auto"),
        ),
        run=RunCfg(
            poll_seconds=_int(env, "POLL_SECONDS", 120),
            run_once=_bool(env, "RUN_ONCE", False),
            dry_run=_bool(env, "DRY_RUN", False),
            overwrite=_bool(env, "OVERWRITE", False),
        ),
        server=ServerCfg(
            host=_get(env, "HTTP_HOST", "0.0.0.0"),
            port=_int(env, "HTTP_PORT", 8088),
        ),
    )
