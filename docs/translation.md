# Translation

Whisper transcribes into whatever is being spoken, and translates natively
*into* English and nowhere else. Every other direction — English audio to
Polish, Japanese audio to French, Spanish audio to German — goes through an
LLM running on Ollama, and that is what this page sets up. Which route a given
request takes is tabled in [How it works](how-it-works.md#routing).

Ask for it the same way you ask for anything: tag the scene `subs:pl`, or
`touch subs.fr` in the folder. Nothing about the language is special-cased,
and nothing needs restarting to add one.

Two limits are worth knowing. The code has to be a bare ISO 639 subtag, or
Stash cannot attach the caption — see
[Language codes](stash-mode.md#language-codes). And the model has to speak
it: `translategemma:4b` covers 55 languages, which is most of what Whisper can
hear but not all of it. A language the model does not know produces a poor
subtitle rather than an error, so spot-check a new one before turning it loose
on a library.

## Setting up Ollama

The example compose file carries an `ollama` service behind a profile:

```bash
docker compose --profile translate up -d ollama
```

Then uncomment `OLLAMA_URL` and `OLLAMA_MODEL` in the worker's environment and
restart it. **No `ollama pull` needed** — the worker checks for the model at
startup and pulls it over the API if missing (`OLLAMA_PULL`), logging progress
as it goes.

The worker reaches Ollama as `http://ollama:11434` over the compose network,
so the example publishes no port: Ollama's API has no authentication.

## Which model

| Model | Size | Notes |
|---|---|---|
| `translategemma:4b` | ~3 GB | Default. Google's purpose-built translation model, 55 languages. Best speed/quality for this job. |
| `translategemma:12b` | ~8 GB | Noticeably better on idiom and register. Roughly 3x slower on CPU. |
| `qwen3:8b` | ~5 GB | Generalist. Handles the batched-context prompt better; useful if you want to tweak the prompt for tone. |

Licences differ. TranslateGemma's weights come under Google's
[Gemma Terms of Use](https://ai.google.dev/gemma/terms), which is not an
open-source licence and binds you to a prohibited-use policy for as long as
you run the model. Qwen3 is released under Apache-2.0. If that
matters for your library, set `OLLAMA_MODEL=qwen3:8b`; it uses the JSON
protocol below and needs no other change.

TranslateGemma is translation-only — it won't return structured JSON. The worker
detects this from the model name and switches to a line-oriented protocol
automatically. Override with `TRANSLATE_MODE=json` or `TRANSLATE_MODE=lines` if
you use a model whose name doesn't give it away.

Subtitle lines are sent `OLLAMA_BATCH` (default 20) at a time. If line counts
come back mismatched, the worker re-runs that batch one line at a time rather
than letting subtitle alignment drift — slower, but it can't silently shift
your timings.

Expect 10–25 minutes per scene on CPU with the 4B model. Worth batching
overnight.

## Turbo cannot translate

`large-v3-turbo`, the default `MODEL`, was fine-tuned on transcription data
with translation data excluded. Asking it for `task="translate"` does **not**
raise an error — it silently returns a transcript in the source language.
Spanish audio tagged `subs:en` would give you Spanish text in a file named
`.en.srt`.

The worker detects turbo models and routes English output elsewhere. You have
two choices:

**Use the LLM** (default, if `OLLAMA_URL` is set). Transcribe with turbo, then
translate the text. Fast transcription, decent translation, one model download.

**Use a second Whisper model.** Set `TRANSLATE_MODEL=large-v3`. Whisper's native
speech-to-English translation is better than translating a transcript, because
it works from the audio. Costs another ~3 GB download and a slower second pass,
and only ever produces English.

With neither configured, non-English audio tagged `subs:en` is skipped with a
clear log message rather than producing a wrong file, and the worker warns
about it at startup. This affects English output only — turbo transcribes
every language it knows, and any target that goes through the LLM is
unaffected either way.

## Translating a video you already have subtitles for

If a transcript in the spoken language is already sitting next to the video,
it is used as the translation source and the audio is not transcribed again.
Tagging `subs:pl` on an English scene that already has `clip.en.srt` costs a
few seconds of language detection instead of several minutes of Whisper.

That applies to any transcript, not only ones this tool wrote — a hand-made or
downloaded one is usually a better source than a fresh machine transcript. Our
own generation marker is stripped before translating, so it never gets fed to
the LLM. Set `REUSE_TRANSCRIPT=0` to always transcribe from the audio;
`REGENERATE=always` implies it, since that is a request to redo the work.

## If translation fails

The source-language transcript is written *before* the translation step, so a
failed or unavailable LLM still leaves you with a usable transcript in the
spoken language (an `.en.srt` for English audio). You lose the translation,
not the transcription work.

A DNS error like `Name or service not known` from the translation step means
the worker cannot reach Ollama; see
[Troubleshooting](troubleshooting.md#ollama-unreachable-or-name-or-service-not-known).
