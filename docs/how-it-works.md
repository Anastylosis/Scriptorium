# How it works

There are two ways in — [a watched folder](folder-mode.md) and
[Stash tags](stash-mode.md) — and they are the same worker underneath. The
longer-term direction is a general self-hosted transcription and translation
service with per-backend plugins; a mount and a Stash library are the two
backends that exist today.

## Routing

| Audio | Wanted | Route |
|---|---|---|
| anything | the language being spoken | Whisper transcribe |
| anything | `subs:auto` | Whisper transcribe in the detected language |
| non-English | `subs:en` | Whisper translate, or `TRANSLATE_MODEL`, or the LLM |
| anything | any other language | Whisper transcribe → LLM translation |

Spanish audio asked for in Spanish is the first row; Japanese audio asked for
in French is the last. Only English has a route of its own, because English is
the only language Whisper can translate *into* — every other target is the
transcript passed to the LLM, whatever the pair. The default turbo model
cannot do the third row by itself; see
[Turbo cannot translate](translation.md#turbo-cannot-translate).

Source language is detected by sampling 45 seconds from the 25%, 50% and 75%
marks of the file rather than trusting the first 30 seconds — intros and music
fool the built-in detection constantly. A file of five minutes or less is
sampled from the start.

Audio is decoded in-process through PyAV, which faster-whisper already depends
on, so the image ships no `ffmpeg` binary and writes no temporary wav files. If
you hit a container PyAV cannot open, an `ffmpeg` on `PATH` is used instead —
add one in a derived image and it will be picked up.

## Hallucination handling

Whisper invents dialogue during long stretches of non-speech. Three defences are
built in:

- **Silero VAD** strips silence before decoding.
- **`condition_on_previous_text=False`** prevents one bad segment from poisoning
  everything after it.
- A post-filter drops segments with high no-speech probability, absurd
  compression ratios, known junk phrases ("Subtitles by...", "Thanks for
  watching", URLs), and any line repeated three times running.

The list is `JUNK_PATTERNS` in `scriptorium/asr.py`. It is not configurable at
runtime, so adding patterns for recurring artefacts specific to your files
means editing that file and building your own image (`make image`, or
`build: .` in the compose file).

A fourth defence is about timing rather than text. Because the VAD decodes a
timeline with the silence cut out, a segment's *end* is restored to the far
side of whatever was removed, and a two-word line can come back holding the
screen until the next person speaks — minutes, on sparse audio. The giveaway
is cues that are exactly contiguous, which real speech never is. Every cue is
therefore capped at 7 seconds (the usual broadcast ceiling for one subtitle),
floored at 1.2 so a single word is readable, and trimmed back if the next cue
starts sooner. Subtitles written before 0.9.1 can hold the screen this way.

## Performance and thread count

Set `THREADS` (default 6) to your **physical performance-core count**, not
total threads. On hybrid Intel CPUs (12th gen and later) the efficiency cores
drag whisper throughput down when work gets spread across them:

| CPU | `THREADS` | Optional pinning |
|---|---|---|
| 6P / 0E | `6` | not needed |
| 8P / 4E | `8` | `cpuset: "0-15"` |
| 8P / 8E | `8` | `cpuset: "0-15"` |

Expect roughly **5–10× realtime** with `large-v3-turbo` at int8 on a modern
x86 desktop or NAS CPU — a 40-minute scene in 4–8 minutes. RAM use is about
2 GB. ARM machines are slower; the image runs there but the numbers above do
not apply.

`BEAM_SIZE=1` is roughly twice as fast at some accuracy cost.

If a particular scene comes out badly — any language — set `MODEL=large-v3`
and ask for it again. Slower, a bit more accurate on accented, quiet or noisy
audio, and a 3 GB download rather than 1.6 GB.

**There is no GPU path today.** The published image carries CPU wheels and no
cuDNN, so `DEVICE=cuda` has nothing to load — and because the model is loaded
lazily, the failure arrives at the first scene rather than at startup. The
worker warns about it on the way up. CPU is the supported configuration and
the numbers above are what it gives you.

## Status page

`http://<host>:8088` (`HTTP_PORT`)

Self-refreshing every 5 seconds. Shows the scene being worked on, a real
progress bar (derived from how far into the media Whisper has decoded), current
speed as a multiple of realtime, an ETA, the detected source language and its
confidence, how many scenes are still queued, recently written files, and the
last 200 log lines. Buttons on the page start a poll now, or pause and resume
the worker.

The page is unauthenticated: do not publish port 8088 to the internet. See
[SECURITY.md](../SECURITY.md#the-status-page).

The running version is in the page footer, and in `/json` as `version`.
Only released images report a real number; a `master` or `sha-` image
reports `0.0.0`, because it was not built from a version tag and its
output should not claim one. The image tag says which build it is.

`http://<host>:8088/json` returns the same state as JSON if you'd rather
poll it from somewhere else.

Nothing to install — it's the Python standard library, running in a thread
alongside the worker. It comes up before the model download, so you can watch
a first start too.
