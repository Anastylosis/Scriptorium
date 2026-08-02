# CLAUDE.md

A worker that watches a Stash library for scenes tagged `subs:<lang>`,
transcribes them with faster-whisper, optionally translates via Ollama, writes
subtitles beside the video, and swaps the tag for `subs:done` / `subs:failed`.

Docker only. Python 3.12. GPL-3.0-only. `README.md` is for users; `PLAN.md`
(untracked) holds working notes.

## Commands

Everything runs in a container; a checkout needs only Docker.

```sh
make check    # ruff + pytest, the same gate CI applies
make image
make pins     # requirements.txt must equal `pip freeze` in the built image
make langs    # regenerate stash_subs/_langtable.py (generated, don't hand-edit)
```

## Layout

```
__main__.py   entry point: logging, config, HTTP server, worker
config.py     env -> frozen dataclasses, injectable so tests need no environ
paths.py      PathMapper: Stash's view of a path -> ours
logsetup.py   stdout + the ring buffer the status page renders
stash.py      GraphQL transport and queries, no policy
tags.py       which subs:<lang> tags exist and what they mean
langs.py      ISO 639 validation
audio.py      PyAV decoding: duration, language-sample windows
asr.py        model cache, transcribe, hallucination filter
subtitles.py  render SRT/VTT, parse, annotate, atomic write
translate.py  Ollama client
captions.py   what Stash already has attached
outcomes.py   per-target results; decides done vs failed, and rescan
worker.py     the queue loop
status.py     state store, status page, POST controls
```

A cue is a plain `(start, end, text)` tuple. There is no Cue class.

## Stash constraints

These cause silent failures, not errors:

- Caption suffixes must be a **bare** ISO 639 subtag. `.pt-BR.srt` fails
  Stash's `ParseBase`, so it is read as part of the filename and the caption
  never attaches — file written, player empty, nothing logged. Hence
  `langs.is_caption_suffix`.
- `.srt` and `.vtt` only. A caption with no language suffix is filed as `"00"`
  and must never compare equal to a real code.
- `metadataScan` is only needed for a **new** (language, extension) pair.
- An unknown GraphQL field fails the **whole query**. Anything
  schema-dependent needs a startup probe with a fallback — see
  `Client.probe_captions`.
- `sceneUpdate` replaces the entire tag list. Re-read tags immediately before
  writing; never write back a poll-time snapshot.
- Whisper knows 100 languages, Stash accepts far more. `is_caption_suffix` and
  `whisper_supports` are separate predicates on purpose.

## Don't "simplify" these

- `whisper_translates()` substring-tests for `"turbo"`. Turbo returns
  **source-language text** for `task="translate"` without erroring — this is
  the only thing stopping Spanish audio landing in `.en.srt`.
- `clean()` consumes segments lazily; that iteration *is* the progress bar.
  Progress fires before filtering so it doesn't stall through junk.
- `condition_on_previous_text=False` and the temperature ladder are
  anti-hallucination measures.
- `metadata_scan` takes the **Stash-side** path, not the mapped local one.
- `start_http()` runs before tag setup and the model pull, so the page is up
  during a multi-gigabyte download.
- The salvage write happens **before** the LLM call.
- The annotation is applied **inside the writer** — that is what guarantees
  the translator can never be handed the marker.
- Request tags are matched by **id** against the plan the query used. Matching
  by name makes a tag created mid-transcription either lose the request or
  reprocess the scene forever.
- `logsetup`'s ring buffer owns a lock separate from the status store. Never
  log while holding the status lock.
- `audio._resample` copies each chunk; `to_ndarray()` is a view into a buffer
  that is freed with the frame.
- The faster-whisper import is lazy inside `Models.get()`, which is why the
  suite runs in seconds with no inference stack.

## Testing

274 tests, ~3s, no network or model downloads. Audio tests synthesise real
media with PyAV; everything else uses fakes at the real seams.

The suite has repeatedly passed while the thing was broken. **After changing
anything that writes a file or talks to Stash, run it** — a stub Stash plus
`MODEL=tiny RUN_ONCE=1` gives a full end-to-end pass in seconds.

## Conventions

- Documentation goes in `.md` files, not comment blocks. Comment the
  non-obvious *why*; don't narrate what the code says.
- No "phase" language in commits or docs.
- Commit messages: imperative subject, then prose naming what was wrong and
  why the fix is shaped as it is.
- A new config value needs `config.py`, the env alias test, and the README
  knobs table.
- Backward compatibility matters; people are running this. `OVERWRITE` still
  means `REGENERATE=always`, and `REQUEST_TAGS` at the old shipped default is
  treated as copied rather than chosen.
- Match the sibling repos (`../fss`, `../StashJanitor`): exact-pinned Actions,
  justified lint exclusions.

## Releasing

Bump `pyproject.toml` **and** `stash_subs/__init__.py`, then tag `vX.Y.Z`.
`release.yml` refuses a tag that disagrees with the declared version — the
version is baked into every subtitle's provenance, so a stale one mislabels
files that outlive the container.

`docker/metadata-action` strips the `v`: git tag `v0.7.1`, image tag `0.7.1`.
Release notes must use `steps.meta.outputs.version`, not `github.ref_name`.

`docker-dev.yml` publishes `master` and `sha-<short>` images for trying a
change on a real Stash before cutting a version.

## Settled, don't re-litigate

- **Go rewrite** — whisper.cpp is ~3x slower than CTranslate2 int8 on CPU, the
  Go bindings are unmaintained, and it loses the progress bar.
- **Standalone binaries** — Docker only, owner's decision.
- **Stash plugin** — exec plugins run inside the Stash container and cannot
  reach a separate worker. Tags are the trigger.
- **YAML config** — env vars are the Docker-native interface.
