# CLAUDE.md

A worker that watches a Stash library for scenes tagged `subs:<lang>`,
transcribes them with faster-whisper, optionally translates via Ollama, writes
subtitles beside the video, and swaps the tag for `subs:done` / `subs:failed`.

With `WATCH_DIRS` set and no Stash, it watches a mount instead: `subs.<lang>`
marker files carry the request and a ledger carries `subs:done`.

Docker only. Python 3.12. GPL-3.0-only. `README.md` is for users; `PLAN.md`
(untracked) holds working notes.

## Commands

Everything runs in a container; a checkout needs only Docker.

```sh
make check    # ruff + pytest, the same gate CI applies
make image
make pins     # requirements.txt must equal `pip freeze` in the built image
make langs    # regenerate scriptorium/_langtable.py (generated, don't hand-edit)
```

## Layout

```
__main__.py   entry point: logging, config, HTTP server, worker
config.py     env -> frozen dataclasses, injectable so tests need no environ
paths.py      PathMapper: Stash's view of a path -> ours
logsetup.py   stdout + the ring buffer the status page renders
stash.py      GraphQL transport and queries, no policy
library.py    the seam: where work comes from, what finishing it means
folder.py     the Stash-less library: marker files, the ledger
tags.py       which subs:<lang> tags exist and what they mean
langs.py      ISO 639 validation
audio.py      PyAV decoding: duration, language-sample windows
asr.py        model cache, transcribe, hallucination filter
subtitles.py  render SRT/VTT, parse, annotate, atomic write
translate.py  Ollama client
captions.py   what Stash already has attached
outcomes.py   per-target results; decides done vs failed, and rescan
worker.py     the queue loop, indifferent to which library it is
status.py     state store, status page, POST controls
```

A cue is a plain `(start, end, text)` tuple. There is no Cue class.

## Folder mode

- `WATCH_DIRS` is the switch, not a missing `STASH_URL`: that has always had a
  default, so unset cannot mean "there is no Stash". Both set is refused.
- `FolderLibrary` synthesises the `captions` list from the subtitles on disk,
  in Stash's shape. That is why `produce()` has no branch for folder mode —
  the "already covered by `foo.eng.srt`" reasoning and `REGENERATE` work
  unchanged on both sides.
- The ledger is `subs:done`. Disk alone cannot be: an `auto` request has no
  destination filename until the detector has run, so every poll would reload
  the model and listen to every file again, and a file with no speech would be
  retried forever.
- The ledger records the stat taken **when the job was queued**, not a fresh
  one at the end. A file that changed during a two-hour transcription has not
  been done, and recording the new mtime buries the new content forever.
- An entry stops counting when the requested languages grow, so `touch
  subs.es` in a finished directory re-opens every video in it.
- The ledger forgets a file only when its root produced **at least one video
  that poll**. An unmounted directory reads as an empty one rather than an
  error, and pruning on that would re-transcribe a library because its NFS
  server blinked.
- `outcomes.Scene.wrote` exists because the ledger cannot reconstruct what
  landed from the target list: the salvaged source transcript is written
  under a language nobody asked for, by a target that then failed.
- `produce()` excludes its own destinations from the "already covered by
  another spelling" check. Reading the caption list off the disk we write to
  otherwise makes every file we wrote cover itself, and `REGENERATE=if-ours`
  a setting that can never fire.

## Stash constraints

These cause silent failures, not errors:

- Caption suffixes must be a **bare** ISO 639 subtag. `.pt-BR.srt` fails
  Stash's `ParseBase`, so it is read as part of the filename and the caption
  never attaches — file written, player empty, nothing logged. Hence
  `langs.is_caption_suffix`.
- `.srt` and `.vtt` only. A caption with no language suffix is filed as `"00"`
  and must never compare equal to a real code.
- `metadataScan` is only needed for a **new** (language, extension) pair.
- A caption attaches only when a scan **walks the subtitle file itself**.
  `task_scan.go` rejects `.srt`/`.vtt` in the scan filter and calls
  `AssociateCaptions` from that rejection branch; `scene.ScanHandler` only
  *cleans* captions, it never attaches them. So a scan must be given the
  **parent directory**. Scanning the video's own path — which is what the
  per-scene Rescan button in the UI does — never reaches the sidecar and
  silently does nothing.
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
- The rescan flush is keyed on **how many scenes a directory has left**,
  not on the path changing between scenes. `sort: "path"` is an
  optimisation the fallback drops, and on an id-sorted queue a directory
  comes back dozens of times — flushing on every change is most of the way
  back to a job per scene.
- `start_http()` runs before tag setup and the model pull, so the page is up
  during a multi-gigabyte download.
- The salvage write happens **before** the LLM call.
- `write()` keeps a per-scene record of destinations. The salvage write
  and `subs:<src>` as a target both claim the source-language file; the
  **second** one is dropped. Dropping the salvage instead would lose the
  transcript when the LLM dies and the source is not itself a target.
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

401 tests, ~7s, no network or model downloads. Audio tests synthesise real
media with PyAV; everything else uses fakes at the real seams.

The suite has repeatedly passed while the thing was broken. **After changing
anything that writes a file or talks to Stash, run it** — a stub Stash plus
`MODEL=tiny RUN_ONCE=1` gives a full end-to-end pass in seconds. Folder mode
needs no stub at all: a directory with a video in it, `WATCH_DIRS=/media
WATCH_MIN_AGE=0 MODEL=tiny RUN_ONCE=1`, and the ledger to read afterwards.

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

## Provenance marker

`MARKER` in `subtitles.py` is a wire contract with moansubs, which parses it
out of subtitle files it ingests. Any node consuming provenance must know a
marker string before a release emits it, so renaming the project could not
just swap the string: `OLD_MARKER = "[stash-subs]"` (this project's name
before the Anastylosis move) stays recognised by `looks_generated()` and
`without_marker()` forever, alongside the current `MARKER = "[scriptorium]"`.
moansubs already does dual-marker detection on its side. The provenance
JSON's `tool` field changed to `"scriptorium"`; the shape
(`tool`/`version`/`asr_model`/`mt_model`/`src`/`dst`/`generated`) did not.

The optional sidecar (`ANNOTATE_SIDECAR=1`) moved from `.stash-subs.json` to
`.scriptorium.json` for new writes. Nothing in this codebase reads a sidecar
back — it is a write-only convenience file — so there is no dual-suffix glob
to maintain here, unlike the marker.

## Releasing

Tag `vX.Y.Z`. That is the whole procedure — there is no version to bump.

The tag reaches the image as the `VERSION` build arg, the Dockerfile turns it
into `SCRIPTORIUM_VERSION`, and `__init__._resolve()` accepts it only if it
looks like a version. `release.yml` checks the tag's shape before building,
because a tag that is not `vX.Y.Z` would not fail anything — it would publish
an image tagged `0.8` whose subtitles all claim `0.0.0`.

Anything with no release tag behind it reports `0.0.0`: dev images, a local
`make image`, the suite. That is deliberate. The version is baked into every
subtitle's provenance and outlives the container, and a dev image used to
stamp whatever number the last release had left in the source.

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
