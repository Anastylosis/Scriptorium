# Provenance wire format

How a machine-generated subtitle file declares itself, so a consumer can tell
it apart from one a person wrote.

**Scriptorium is the producer.** This directory is the contract's home; any
change here is a change every consumer must follow in the same release.

Consumers:

| consumer | language | what it does with this |
|---|---|---|
| MoanSubs | Go | flags uploads as machine-generated, and distinguishes translations |
| Scriptorium | Python | recognises its own output so a re-run never overwrites hand-made subtitles |

## Why it fails open

Detection determines generated status **from the file**, never from what an
uploader claims. If a consumer stops recognising the format, uploads silently
lose their disclosure — no error, no warning, just a machine transcript
presented as if a person wrote it.

That is why the fixtures exist. There is no build-time coupling between a
Python producer and a Go consumer; the fixtures are the only thing that makes
a format change visible on both sides.

## The marker

A visible annotation cue carries a sentinel:

```
[scriptorium]
```

Files written before the `stash-subs` → Scriptorium rename carry the previous
sentinel, and it must be recognised **forever** — those files exist in the
wild and do not get rewritten:

```
[stash-subs]
```

A consumer accepts either. A producer emits only the current one.

## Where detection looks

The **first 4096 bytes and the last 4096 bytes**, not the whole file.

A full read on every upload is not worth it, and the annotation cue is placed
at the end, so the tail window is the one that usually matters. On a file
shorter than 4096 bytes the windows overlap, which is harmless.

**Known blind spot:** a marker in neither window is not found. This is
deliberate and pinned by `marker-buried.vtt`. Widening the search is a
decision to take on purpose.

## The NOTE block

Full provenance travels inside the subtitle file, in a WebVTT `NOTE` block —
invisible to every player, so it survives being moved around without a
sidecar.

```
NOTE
{"asr_model": "large-v3", "cues": 3, "dst": "en", ...}

```

Framing rules, all load-bearing:

- The literal line `NOTE`, then the body, then a **blank line**.
- A blank line terminates a NOTE block, so the body must never contain one.
  The producer collapses runs of blank lines before writing.
- The producer writes `\n`. Consumers should accept `\r\n` too — files get
  moved between systems.
- JSON keys are sorted (`sort_keys=True`), so byte-comparing two renders of
  the same provenance is meaningful.

## JSON payload

| key | type | meaning |
|---|---|---|
| `tool` | string | producer name, currently `scriptorium` |
| `version` | string | producer version |
| `asr_model` | string | speech-recognition model |
| `mt_model` | string or **null** | translation model; **null when no translation ran** |
| `src` | string | source language, ISO 639 |
| `dst` | string | target language, ISO 639 |
| `generated` | string | ISO date. Note the key is `generated`, **not** `date` |
| `cues` | int | optional, number of cues |
| `media` | string | optional, media fingerprint |

Two traps worth stating plainly, because both are easy to get wrong and
neither fails loudly:

- **`mt_model` is `null`, not absent**, when nothing was translated. A
  consumer must treat null as "not translated". Go's `encoding/json` leaves a
  non-pointer `string` untouched on a null, which gives the right answer by
  accident — pinned by `transcript.vtt` so a later change to `*string` is
  caught.
- **The date key is `generated`.** A consumer modelling it as `date` silently
  gets an empty value.

`src == dst` means a transcript. `mt_model` non-null means a machine
translation of a machine transcription, which is materially worse than either
alone and is worth surfacing differently.

## Fixtures

| fixture | asserts |
|---|---|
| `transcript.vtt` | plain transcript; `mt_model` null; optional `cues`/`media` present |
| `translation.vtt` | `mt_model` set, `src != dst` |
| `legacy-marker.vtt` | the pre-rename sentinel is still detected |
| `human.vtt` | no marker → **not** generated (a false positive mislabels someone's own work) |
| `marker-in-tail.vtt` | file exceeds both windows; only the tail sniff can find it |
| `marker-buried.vtt` | marker in neither window → **not** detected, pinning the blind spot |

Regenerate with:

```sh
python3 spec/provenance/generate.py
```

**Fixtures are append-only.** An old format must keep passing forever, so
never edit an existing fixture — add a new one. If the generator's output for
an existing fixture changes, that is a wire-format change, and every consumer
needs updating in the same release.
