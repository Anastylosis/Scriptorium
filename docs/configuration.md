# Configuration

Everything is an environment variable, and every one has a default, so an
empty environment is a working setup. A variable set to the empty string is
treated as unset. Booleans accept `1`, `true`, `yes` or `on`; anything else is
off.

Test with `DRY_RUN=1` on two or three scenes before letting it loose.

## Running

| Variable | Default | Notes |
|---|---|---|
| `DRY_RUN` | `0` | Log what would be written, change nothing, leave tags alone |
| `RUN_ONCE` | `0` | Drain the queue and exit, for cron-style use |
| `POLL_SECONDS` | `120` | Queue poll interval |
| `REGENERATE` | `never` | `never`, `if-ours`, `always` — see [Regenerating](generated-subtitles.md#regenerating) |
| `OVERWRITE` | `0` | Older spelling; `1` means `REGENERATE=always`. Ignored when `REGENERATE` is set |
| `REUSE_TRANSCRIPT` | `1` | Translate from an existing transcript instead of re-transcribing |
| `HTTP_HOST` | `0.0.0.0` | Status page bind address — see [SECURITY.md](../SECURITY.md#the-status-page) |
| `HTTP_PORT` | `8088` | Status page port |

## Transcription

| Variable | Default | Notes |
|---|---|---|
| `MODEL` | `large-v3-turbo` | Whisper model. `large-v3` is slower, a bit more accurate, and can translate to English |
| `MODEL_DIR` | `/models` | Where models are downloaded; mount a volume here |
| `THREADS` | `6` | Physical performance cores — see [Performance](how-it-works.md#performance-and-thread-count) |
| `BEAM_SIZE` | `5` | Lower to `1` for ~2× speed at some accuracy cost |
| `COMPUTE_TYPE` | `int8` | CTranslate2 compute type |
| `DEVICE` | `cpu` | The published image is CPU-only; anything else is warned about at startup and fails at the first scene |
| `TRANSLATE_MODEL` | unset | Second Whisper model for speech→English, e.g. `large-v3` — see [Turbo cannot translate](translation.md#turbo-cannot-translate) |

## Translation

| Variable | Default | Notes |
|---|---|---|
| `OLLAMA_URL` | unset | e.g. `http://ollama:11434`. Unset means no LLM translation |
| `OLLAMA_MODEL` | `translategemma:4b` | See [Which model](translation.md#which-model) |
| `OLLAMA_PULL` | `1` | Auto-pull the model at startup |
| `TRANSLATE_MODE` | `auto` | `json`, `lines`, or auto-detect from model name |
| `OLLAMA_BATCH` | `20` | Subtitle lines per translation request |

## Output and annotation

| Variable | Default | Notes |
|---|---|---|
| `OUTPUT_FORMATS` | `srt` | `srt`, `vtt`, or `srt,vtt` |
| `ANNOTATE` | `end` | `none`, `start`, `end` |
| `ANNOTATE_TEXT` | built-in | Template for the marker cue — see [Generated subtitles](generated-subtitles.md) |
| `ANNOTATE_SECONDS` | `3.0` | How long the marker shows |
| `ANNOTATE_GAP` | `1.0` | Pause after the last real cue |
| `ANNOTATE_SIDECAR` | `0` | Also write `<subtitle>.scriptorium.json` |

## Stash mode

Named at startup and ignored in folder mode.

| Variable | Default | Notes |
|---|---|---|
| `STASH_URL` | `http://stash:9999` | Leave unset in folder mode; setting it alongside `WATCH_DIRS` is refused |
| `STASH_API_KEY` | unset | Needed when Stash has authentication on: Settings → Security |
| `PATH_FROM` | `/data` | Path prefix as Stash reports it |
| `PATH_TO` | `/data` | The same directory as this container sees it |
| `TAG_DISCOVERY` | `auto` | `true` accepts any `subs:<lang>` tag; `false` pins the list to `REQUEST_TAGS`; `auto` discovers unless `REQUEST_TAGS` was narrowed |
| `REQUEST_TAGS` | `subs:en,subs:es,subs:auto` | Fixed request list. Setting it to anything but this default turns discovery off under `auto` |
| `CREATE_TAGS` | `subs:en` | Tags made at startup; others are yours to create |
| `IGNORE_TAGS` | unset | `subs:` tags to skip entirely |
| `DONE_TAG` | `subs:done` | Replaces the request tag on success |
| `FAILED_TAG` | `subs:failed` | Replaces the request tag on failure |

## Folder mode

| Variable | Default | Notes |
|---|---|---|
| `WATCH_DIRS` | unset | Directories to watch, comma-separated. Setting it is what turns folder mode on. Leave `STASH_URL` unset |
| `WATCH_LANGS` | `auto` | What a video with no `subs.<lang>` marker near it asks for |
| `STATE_DIR` | `/state` | Where folder mode keeps its ledger |
| `WATCH_MIN_AGE` | `60` | Seconds a file must be untouched before it is picked up |
| `WATCH_RETRY_FAILED` | `0` | Retry files that failed on an earlier run |
| `WATCH_EXTENSIONS` | `mp4,mkv,m4v,mov,avi,webm,wmv,flv,ts,mpg,mpeg` | Video extensions to look for |
