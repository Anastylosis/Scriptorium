# stash-subs

Tag-driven subtitle generation for Stash. You mark the scenes you want; nothing
else in the library is ever read.

## Install

Copy `docker-compose.example.yml` to `docker-compose.yml`, point the first
volume at your video library, and start it:

```sh
curl -O https://raw.githubusercontent.com/Wasylq/stash-subs/master/docker-compose.example.yml
mv docker-compose.example.yml docker-compose.yml
$EDITOR docker-compose.yml
docker compose up -d
```

Images are published for `linux/amd64` and `linux/arm64`:

```sh
docker pull ghcr.io/wasylq/stash-subs:latest
```

The worker needs to see your videos at the same path Stash reports for them.
If Stash has `/data/movie.mp4` mounted from `/volume1/media`, mount the same
host directory at `/data` here. If you cannot, set `PATH_FROM` and `PATH_TO`
to map between the two.

## How you use it

1. In Stash, select one or more scenes (checkbox in the top-left of each card).
2. **Edit** → add a tag:
   - `subs:en` — English subtitles
   - `subs:es` — Spanish subtitles
   - `subs:auto` — subtitles in whatever language is actually spoken
   You can add more than one; each produces its own file.
3. Wait. The worker polls every 2 minutes and processes the queue one scene at
   a time.
4. When a scene is finished the request tag is replaced with `subs:done`
   (or `subs:failed`), and the containing folder is rescanned so the caption
   shows up in the player.

The tags are created automatically on first run, so you don't need to make them
by hand.

Because the queue *is* a Stash filter, you can watch progress by browsing to the
`subs:en` tag — the list shrinks as work completes.

## Status page

`http://<host>:8088`

Self-refreshing every 5 seconds. Shows the scene being worked on, a real
progress bar (derived from how far into the media Whisper has decoded), current
speed as a multiple of realtime, an ETA, the detected source language and its
confidence, how many scenes are still queued, recently written files, and the
last 200 log lines.

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
re-tag it. Slower, a bit more accurate on accented or noisy audio.

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

### If translation fails

The source-language transcript is written *before* the translation step, so a
failed or unavailable LLM still leaves you with a usable `.en.srt`. You lose the
translation, not the transcription work.

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
| `OVERWRITE` | `0` | Regenerate even if the `.srt` already exists |
| `POLL_SECONDS` | `120` | Queue poll interval |
| `HTTP_PORT` | `8088` | Status page port |
| `OLLAMA_PULL` | `1` | Auto-pull the model at startup |
| `TRANSLATE_MODE` | `auto` | `json`, `lines`, or auto-detect from model name |
| `TRANSLATE_MODEL` | unset | Second Whisper model for speech→English, e.g. `large-v3` |
| `OLLAMA_BATCH` | `20` | Subtitle lines per translation request |
| `BEAM_SIZE` | `5` | Lower to `1` for ~2× speed at some accuracy cost |

Test with `DRY_RUN=1` on two or three scenes before letting it loose.

## Troubleshooting

**Captions don't appear after processing.** Stash has historically been finicky
about detecting caption files added to already-scanned scenes. Run a manual scan
of that directory from Settings → Tasks. Confirm the naming is exactly
`scene.mp4` + `scene.en.srt` — same folder, same basename, two-letter code.

**`Stash HTTP 401`.** Authentication is on. Generate an API key in Settings →
Security and set `STASH_API_KEY`.

**GraphQL field errors.** Stash's schema shifts between releases. Open
`http://<host>:9999/playground` and check the mutation and filter names against
what the worker sends.

**Path not visible to this container.** Stash and the worker must see the same
video at the same container path. If they cannot, set `PATH_FROM` / `PATH_TO`
to map between them.
