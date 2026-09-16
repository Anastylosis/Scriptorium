# Scriptorium

[![CI](https://github.com/Anastylosis/Scriptorium/actions/workflows/ci.yml/badge.svg)](https://github.com/Anastylosis/Scriptorium/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Anastylosis/Scriptorium/branch/master/graph/badge.svg)](https://codecov.io/gh/Anastylosis/Scriptorium)

*(formerly stash-subs)*

Subtitles for a video library you host yourself. faster-whisper transcribes,
Ollama translates if you want a language the audio is not in, and the subtitle
file lands beside the video where any player will look for it.

There are two ways in, and they are the same worker underneath:

- **Watch a folder.** Point it at a mount and it gets on with it. `touch
  subs.en` in a directory is how you ask that shelf for English; with no
  marker at all it transcribes whatever is spoken.
  → [Without Stash](#without-stash-watching-a-folder)
- **Take requests from Stash.** Tag a scene `subs:en`, `subs:auto`, or any
  language you like, and it is picked up on the next poll — the tag is
  swapped for `subs:done` and the caption attached. You mark the scenes you
  want; nothing else in the library is ever read.
  → [How you use it](#how-you-use-it)

The longer-term direction is a general self-hosted transcription and
translation service with per-backend plugins. A mount and a Stash library are
the two backends that exist today.

## Install

To see it work, one command — point it at a folder of videos and watch the
status page while it goes:

```sh
docker run --rm -p 8088:8088 \
  -v /path/to/your/videos:/media \
  -v scriptorium-models:/models \
  -v scriptorium-state:/state \
  -e WATCH_DIRS=/media -e RUN_ONCE=1 \
  ghcr.io/anastylosis/scriptorium:latest
```

That transcribes whatever is spoken in each file, writes the subtitle beside
it, and exits. The first run downloads the model — about 1.6 GB for the
default `large-v3-turbo` — into the `scriptorium-models` volume, so it is
paid for once. After that, expect roughly 5–10× realtime on a modern CPU: a
40-minute scene in 4–8 minutes, using about 2 GB of RAM. Drop `RUN_ONCE=1`
to leave it watching, and see
[Without Stash](#without-stash-watching-a-folder) for asking for particular
languages.

To keep it running, copy `docker-compose.example.yml` to
`docker-compose.yml`, point the first volume at your video library, and
start it:

```sh
curl -O https://raw.githubusercontent.com/Anastylosis/Scriptorium/master/docker-compose.example.yml
mv docker-compose.example.yml docker-compose.yml
$EDITOR docker-compose.yml
docker compose up -d
```

Images are published for `linux/amd64` and `linux/arm64`:

```sh
docker pull ghcr.io/anastylosis/scriptorium:latest
```

The worker needs to see your videos at the same path Stash reports for them.
If Stash has `/data/movie.mp4` mounted from `/volume1/media`, mount the same
host directory at `/data` here. If you cannot, set `PATH_FROM` and `PATH_TO`
to map between the two.

Watching a folder instead of a Stash library? The same example file carries
that service, commented out — mount your media, set `WATCH_DIRS`, and none of
the path-matching above applies. See
[Without Stash](#without-stash-watching-a-folder).

## How you use it

1. In Stash, select one or more scenes (checkbox in the top-left of each card).
2. **Edit** → add a tag:
   - `subs:en` — English subtitles
   - `subs:auto` — subtitles in whatever language is actually spoken
   - `subs:<code>` — any language, see below

   You can add more than one; each produces its own file.
3. Wait. The worker polls every 2 minutes and processes the queue one scene at
   a time.
4. When a scene is finished the request tag is replaced with `subs:done`
   (or `subs:failed`), and the containing folder is rescanned so the caption
   shows up in the player.

A scene is only marked `subs:done` if every language it asked for was either
produced or already present. If one could not be made — a target needing an
LLM with no `OLLAMA_URL` set, for instance — the scene is marked `subs:failed`
even though the others succeeded, so the request stays visible instead of
disappearing into a `done` pile. The log names each language and what happened
to it:

```
es: unsupported (tiny cannot translate and OLLAMA_URL is unset)
en: written (412 cues)
```

A scene with no speech in it counts as done, not failed — there was nothing
to produce.

Only `subs:en`, `subs:done` and `subs:failed` are created for you. To request
another language, make the tag yourself — `subs:fr`, `subs:ja`, `subs:pl` —
and it is picked up on the next poll. No restart, no configuration.

Because the queue *is* a Stash filter, you can watch progress by browsing to the
`subs:en` tag — the list shrinks as work completes.

### Language codes

Use a bare two- or three-letter ISO 639 code. `subs:pt` and `subs:por` both mean
Portuguese and produce the same file.

**Do not use a regional code like `subs:pt-BR` or `subs:en-US`.** Stash cannot
parse a caption suffix that carries a region, so it treats `.pt-BR.srt` as part
of the filename and the subtitle silently never attaches to the scene — the file
is written and nothing appears in the player. The worker refuses these and tells
you which code to use instead:

```
tag subs:pt-BR ignored: Stash cannot attach captions with a regional subtag
('.pt-BR.srt' is parsed as part of the filename, so the caption silently
never attaches). Use subs:pt.
```

A tag that is not a language at all is reported differently, so you can tell a
typo from a valid-but-unusable code. Both are logged once, when first seen,
rather than on every poll.

Whisper can transcribe 100 languages. Anything outside that set can still be
reached by translating from the spoken language, which needs `OLLAMA_URL`.

## Without Stash: watching a folder

Set `WATCH_DIRS` and there is no Stash in the picture: the worker walks the
mount, transcribes what it finds, and writes the subtitle beside the video.
Nothing else changes — same models, same marker, same status page.

```yaml
services:
  scriptorium:
    image: ghcr.io/anastylosis/scriptorium:latest
    volumes:
      - /path/to/your/videos:/media
      - scriptorium-state:/state       # small; holds the ledger
      - scriptorium-models:/models
    environment:
      - WATCH_DIRS=/media
```

`STASH_URL` must be left unset — it has a default, so setting both is refused
rather than guessed at.

The subtitle is written as `<video stem>.<lang>.srt` beside the video, which
is the sidecar convention Plex, Jellyfin, Kodi and VLC all pick up with no
extra step — though a library server generally wants a scan before it notices.

> **If Stash manages this library, use Stash mode.** Folder mode writes the
> subtitle correctly and then stops, because attaching a caption to a scene is
> something only a Stash scan does. You would get files on disk, a player with
> no subtitle track, and nothing in any log to say why. Folder mode is for
> libraries Stash has never heard of.

Stash-only settings — `PATH_FROM`, `PATH_TO`, `REQUEST_TAGS`, `STASH_API_KEY`
and the rest — are named at startup and ignored, so a compose file carried
across from a Stash setup does not quietly half-work.

**Asking for a language.** What a tag does in Stash, an empty file does here:

```
/media/spanish/subs.en          everything under /media/spanish wants English
/media/spanish/subs.es          ...and Spanish; several markers may sit together
/media/spanish/otra.subs.auto   just this video, the language actually spoken
```

`touch subs.en` is the whole gesture. The **nearest** marker wins: one deeper
in the tree replaces the one above it rather than adding to it, and a video
with its own `<name>.subs.<lang>` ignores its directory entirely. With no
marker anywhere, `WATCH_LANGS` decides — and its default is `auto`, meaning
transcribe whatever is spoken.

The code follows the same rules as a tag: a bare two- or three-letter ISO 639
subtag, so `subs.pt-BR` is refused and logged. See
[Language codes](#language-codes).

**What stops it doing the same file twice.** `subs:done` has no equivalent on
a filesystem, so the worker keeps a ledger in `STATE_DIR`. A video is offered
again when it changes, when a marker asks for a language it has not been
asked for before, or when a subtitle it wrote is deleted. A failure is
remembered the way `subs:failed` is; `WATCH_RETRY_FAILED=1` retries anyway,
and deleting the ledger costs a re-detection, never a subtitle. Entries for
videos that are no longer there are dropped as they are noticed, so the file
stays about as long as your library rather than growing forever.

Subtitles already sitting beside a video are read the way Stash's caption list
is, so a hand-made `movie.eng.srt` still means `movie.en.srt` is pointless,
and `REGENERATE` behaves exactly as it does with Stash.

A file whose mtime is less than `WATCH_MIN_AGE` seconds old is left alone —
half a copied-in mkv transcribes as half a film, and nothing would come back
to finish it.

Every poll walks the whole tree, so on a large library on spinning disks or a
network mount, raise `POLL_SECONDS` — an hour is a reasonable interval for a
folder you add to occasionally. Mount `/state` somewhere real: the ledger is
what stops a rebuilt container listening to the entire library again.

## Generated subtitles say so

Every file the worker writes ends with a short cue naming what produced it:

```
[scriptorium] machine-generated subtitles · large-v3-turbo + translategemma:4b · Spanish → English · 2026-08-02
```

A transcript that was not translated names one language rather than two.

It sits **after** the last line of dialogue by default, so it never covers the
opening shot and never overlaps speech. On a scene where dialogue runs to the
final frame the marker extends a few seconds past the end of the video, which
is deliberate: tools that pair subtitles to scenes by runtime allow about
twenty seconds of slack, and a marker crammed backwards over the closing line
would be worse. A marker long enough to distort that signal is reined in.

Set `ANNOTATE=start` to put it first instead — useful if you want to know what
made a file before watching it. It is skipped automatically when dialogue
begins too early to fit. `ANNOTATE=none` turns it off, and produces output
byte-identical to having never enabled it.

`ANNOTATE_TEXT` takes a template. Available placeholders: `{marker}`, `{tool}`,
`{version}`, `{asr_model}`, `{mt_model}`, `{mt_suffix}`, `{src}`, `{src_name}`,
`{dst}`, `{dst_name}`, `{languages}`, `{date}`. It must contain `{marker}`, and
is checked when the container starts rather than part-way through a
transcription.

### Regenerating

The `[scriptorium]` marker is how a later run recognises its own work. Files
carrying the `[stash-subs]` marker — this project's name before it was
renamed — are recognised too, forever:

| `REGENERATE` | Behaviour |
|---|---|
| `never` (default) | Leave any existing subtitle alone |
| `if-ours` | Replace files carrying the marker; never touch hand-made or downloaded ones |
| `always` | Replace everything |

`if-ours` is the one worth knowing about — it lets you re-run the whole library
with a better model without destroying subtitles you wrote or sourced yourself.
It only recognises files written with an annotation, so it does nothing useful
for anything produced while `ANNOTATE=none`.

`OVERWRITE=1` still works and means `always`.

### VTT and provenance

`OUTPUT_FORMATS=vtt` writes WebVTT instead of SubRip; `OUTPUT_FORMATS=srt,vtt`
writes both, which gives Stash two caption tracks for the same language.

WebVTT has real comments, so a `NOTE` block at the top carries the full
provenance as JSON — the models, the languages, the date, the cue count. It is
invisible to every player. The visible marker cue is still written; the note is
extra.

`ANNOTATE_SIDECAR=1` writes the same JSON to `<subtitle>.scriptorium.json`. Off
by default, because it puts a second file in your media folder to record
something the marker already says. Stash ignores the extension.

## Work Stash already has

The worker asks Stash which captions a scene already carries, so it does not
redo work or make Stash redo work.

A language you already have is skipped even when it is spelled differently: if
a hand-placed `clip.eng.srt` is attached to the scene, tagging it `subs:en`
produces nothing rather than a near-duplicate `clip.en.srt`. A caption Stash
lists but whose file you have since deleted does *not* count — that gets
regenerated.

Stash is only asked to rescan when a genuinely new language lands. Replacing a
caption it already knows about is picked up from disk on its own, so no scan is
triggered for it.

The ask is made as soon as the queue holds no more scenes in that directory —
so a folder's subtitles show up when the worker moves off it, not at the end of
a run that may take days — and at least every ten minutes for a directory big
enough that the queue never leaves it.

On a Stash too old to report captions the worker notices at startup, says so
once, and falls back to checking the filesystem — everything still works, it
just cannot spot equivalent spellings.

## Status page

`http://<host>:8088`

Self-refreshing every 5 seconds. Shows the scene being worked on, a real
progress bar (derived from how far into the media Whisper has decoded), current
speed as a multiple of realtime, an ETA, the detected source language and its
confidence, how many scenes are still queued, recently written files, and the
last 200 log lines.

The running version is in the page footer, and in `/json` as `version`.
Only released images report a real number; a `master` or `sha-` image
reports `0.0.0`, because it was not built from a version tag and its
output should not claim one. The image tag says which build it is.

`http://<host>:8088/json` returns the same state as JSON if you'd rather
poll it from somewhere else.

Nothing to install — it's the Python standard library, running in a thread
alongside the worker.

## Important: turbo cannot translate

`large-v3-turbo` was fine-tuned on transcription data with translation data
excluded. Asking it for `task="translate"` does **not** raise an error — it
silently returns a transcript in the source language. Spanish audio tagged
`subs:en` would give you Spanish text in a file named `.en.srt`.

The script detects turbo models and routes English output elsewhere. You have
two choices:

**Use the LLM** (default, if `OLLAMA_URL` is set). Transcribe with turbo, then
translate the text. Fast transcription, decent translation, one model download.

**Use a second Whisper model.** Set `TRANSLATE_MODEL=large-v3`. Whisper's native
speech-to-English translation is better than translating a transcript, because
it works from the audio. Costs another ~3 GB download and a slower second pass,
and only ever produces English.

With neither configured, non-English audio tagged `subs:en` is skipped with a
clear log message rather than producing a wrong file.

## What happens under the hood

| Audio | Wanted | Route |
|---|---|---|
| Spanish | `subs:es` | Whisper transcribe |
| Spanish | `subs:en` | LLM, or `TRANSLATE_MODEL` — see above |
| English | `subs:en` | Whisper transcribe |
| English | `subs:es` | Whisper transcribe → Ollama translation |
| anything | `subs:auto` | Whisper transcribe in the detected language |

Whisper only translates *into* English, which is why the last row needs the LLM.

Source language is detected by sampling 45 seconds from the 25%, 50% and 75%
marks of the file rather than trusting the first 30 seconds — intros and music
fool the built-in detection constantly.

Audio is decoded in-process through PyAV, which faster-whisper already depends
on, so the image ships no `ffmpeg` binary and writes no temporary wav files. If
you hit a container PyAV cannot open, an `ffmpeg` on `PATH` is used instead —
add one in a derived image and it will be picked up.

## Performance and thread count

Set `THREADS` to your **physical performance-core count**, not total threads.
On hybrid Intel CPUs (12th gen and later) the efficiency cores drag whisper
throughput down when work gets spread across them:

| CPU | `THREADS` | Optional pinning |
|---|---|---|
| 6P / 0E | `6` | not needed |
| 8P / 4E | `8` | `cpuset: "0-15"` |
| 8P / 8E | `8` | `cpuset: "0-15"` |

Expect roughly **5–10× realtime** with `large-v3-turbo` at int8 on a modern
x86 desktop or NAS CPU — a 40-minute scene in 4–8 minutes. RAM use is about
2 GB. ARM machines are slower; the image runs there but the numbers above do
not apply.

If Spanish output disappoints on a particular scene, set `MODEL=large-v3` and
re-tag it. Slower, a bit more accurate on accented or noisy audio, and a 3 GB
download rather than 1.6 GB.

**There is no GPU path today.** The published image carries CPU wheels and no
cuDNN, so `DEVICE=cuda` has nothing to load — and because the model is loaded
lazily, the failure arrives at the first scene rather than at startup. The
worker warns about it on the way up. CPU is the supported configuration and
the numbers above are what it gives you.

## Optional: English → Spanish

Only needed if you want Spanish subs on English audio. Whisper translates *into*
English natively, so every other direction needs an LLM.

```bash
docker compose --profile translate up -d ollama
```

Then uncomment `OLLAMA_URL` and `OLLAMA_MODEL` in the worker's environment and
restart it. **No `ollama pull` needed** — the worker checks for the model at
startup and pulls it over the API if missing, logging progress as it goes.

### Which model

| Model | Size | Notes |
|---|---|---|
| `translategemma:4b` | ~3 GB | Default. Google's purpose-built translation model, 55 languages. Best speed/quality for this job. |
| `translategemma:12b` | ~8 GB | Noticeably better on idiom and register. Roughly 3x slower on CPU. |
| `qwen3:8b` | ~5 GB | Generalist. Handles the batched-context prompt better; useful if you want to tweak the prompt for tone. |

TranslateGemma is translation-only — it won't return structured JSON. The script
detects this from the model name and switches to a line-oriented protocol
automatically. Override with `TRANSLATE_MODE=json` or `TRANSLATE_MODE=lines` if
you use a model whose name doesn't give it away.

If line counts come back mismatched, the script re-runs that batch one line at a
time rather than letting subtitle alignment drift — slower, but it can't
silently shift your timings.

Expect 10–25 minutes per scene on CPU with the 4B model. Worth batching
overnight.

### Translating a scene you already have subtitles for

If a transcript in the spoken language is already sitting next to the video,
it is used as the translation source and the audio is not transcribed again.
Tagging `subs:pl` on an English scene that already has `clip.en.srt` costs a
few seconds of language detection instead of several minutes of Whisper.

That applies to any transcript, not only ones this tool wrote — a hand-made or
downloaded one is usually a better source than a fresh machine transcript. Our
own generation marker is stripped before translating, so it never gets fed to
the LLM. Set `REUSE_TRANSCRIPT=0` to always transcribe from the audio;
`REGENERATE=always` implies it, since that is a request to redo the work.

### If translation fails

The source-language transcript is written *before* the translation step, so a
failed or unavailable LLM still leaves you with a usable `.en.srt`. You lose the
translation, not the transcription work.

A DNS error like `Name or service not known` from the translation step means
the worker cannot reach Ollama. The `ollama` service in the example compose
sits behind a profile, so `docker compose up -d` does not start it:

```sh
docker compose --profile translate up -d
```

Setting `OLLAMA_URL` on its own is not enough. The same problem is reported at
startup as `Ollama unreachable at ...`.

## Hallucination handling

Whisper invents dialogue during long stretches of non-speech. Three defences are
built in:

- **Silero VAD** strips silence before decoding.
- **`condition_on_previous_text=False`** prevents one bad segment from poisoning
  everything after it.
- A post-filter drops segments with high no-speech probability, absurd
  compression ratios, known junk phrases ("Subtitles by...", "Thanks for
  watching", URLs), and any line repeated three times running.

Add your own patterns to `JUNK_PATTERNS` in the script if you see recurring
artefacts specific to your files.

## Useful knobs

| Variable | Default | Notes |
|---|---|---|
| `DRY_RUN` | `0` | Log what would be written, change nothing, leave tags alone |
| `RUN_ONCE` | `0` | Drain the queue and exit, for cron-style use |
| `REGENERATE` | `never` | `never`, `if-ours`, `always` — see above |
| `ANNOTATE` | `end` | `none`, `start`, `end` |
| `ANNOTATE_TEXT` | built-in | Template for the marker cue |
| `ANNOTATE_SECONDS` | `3.0` | How long the marker shows |
| `ANNOTATE_GAP` | `1.0` | Pause after the last real cue |
| `ANNOTATE_SIDECAR` | `0` | Also write `<subtitle>.scriptorium.json` |
| `OUTPUT_FORMATS` | `srt` | `srt`, `vtt`, or `srt,vtt` |
| `REUSE_TRANSCRIPT` | `1` | Translate from an existing transcript instead of re-transcribing |
| `POLL_SECONDS` | `120` | Queue poll interval |
| `HTTP_PORT` | `8088` | Status page port |
| `OLLAMA_PULL` | `1` | Auto-pull the model at startup |
| `TRANSLATE_MODE` | `auto` | `json`, `lines`, or auto-detect from model name |
| `TRANSLATE_MODEL` | unset | Second Whisper model for speech→English, e.g. `large-v3` |
| `OLLAMA_BATCH` | `20` | Subtitle lines per translation request |
| `BEAM_SIZE` | `5` | Lower to `1` for ~2× speed at some accuracy cost |
| `TAG_DISCOVERY` | `auto` | `false` pins the list to `REQUEST_TAGS` |
| `CREATE_TAGS` | `subs:en` | Tags made at startup; others are yours to create |
| `IGNORE_TAGS` | unset | `subs:` tags to skip entirely |
| `WATCH_DIRS` | unset | Folder mode: directories to watch, comma-separated. Leave `STASH_URL` unset |
| `WATCH_LANGS` | `auto` | What a video with no `subs.<lang>` marker near it asks for |
| `STATE_DIR` | `/state` | Where folder mode keeps its ledger |
| `WATCH_MIN_AGE` | `60` | Seconds a file must be untouched before it is picked up |
| `WATCH_RETRY_FAILED` | `0` | Retry files that failed on an earlier run |
| `WATCH_EXTENSIONS` | mp4,mkv,… | Video extensions to look for |

Test with `DRY_RUN=1` on two or three scenes before letting it loose.

## Troubleshooting

**Captions don't appear after processing.** Stash attaches a caption only when
a scan walks the *subtitle file itself*. It rejects `.srt` and `.vtt` as media
and then associates them from that rejection branch, so the scan has to be
pointed at a path whose walk reaches the sidecar.

The per-scene **Rescan** button does not: it scans the video file's own path,
the walk contains one `.mp4`, and the `.srt` beside it is never enumerated.
Nothing is attached, and nothing is logged. Rescanning the scene as many times
as you like will not change that.

Scan the **directory** instead, from Settings → Tasks. Confirm the naming is
exactly `scene.mp4` + `scene.en.srt` — same folder, same basename, bare
two-letter code.

**`Stash HTTP 401`.** Authentication is on. Generate an API key in Settings →
Security and set `STASH_API_KEY`.

**GraphQL field errors.** Stash's schema shifts between releases. Open
`http://<host>:9999/playground` and check the mutation and filter names against
what the worker sends.

**Path not visible to this container.** Stash and the worker must see the same
video at the same container path. If they cannot, set `PATH_FROM` / `PATH_TO`
to map between them.

## License

Copyright (C) 2026 Wasylq

[GPL-3.0-only](LICENSE).
