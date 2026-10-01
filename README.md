# Scriptorium

[![CI](https://github.com/Anastylosis/Scriptorium/actions/workflows/ci.yml/badge.svg)](https://github.com/Anastylosis/Scriptorium/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/Anastylosis/Scriptorium/branch/master/graph/badge.svg)](https://codecov.io/gh/Anastylosis/Scriptorium)

Subtitles for a video library you host yourself. faster-whisper transcribes,
Ollama translates if you want a language the audio is not in, and the subtitle
file lands beside the video where any player will look for it.

**You do not need Stash.** Watching a folder is a complete way to run it on
its own; Stash is one optional source of requests, not a requirement. Both are
the same worker underneath. *(Formerly stash-subs.)*

## What it needs

- **Docker.** Images for `linux/amd64` and `linux/arm64`.
- **A CPU.** There is no GPU path. Expect roughly 5–10× realtime on a modern
  x86 CPU — a 40-minute video in 4–8 minutes — using about 2 GB of RAM.
- **About 1.6 GB of disk for the model**, downloaded once on first run
  (the default `large-v3-turbo`).
- **Optionally, Ollama** — only for target languages other than English or
  the one being spoken. See [Translating](#translating-into-other-languages).

## Try it

Point it at a folder of videos and watch `http://localhost:8088` while it goes:

```sh
docker run --rm -p 8088:8088 \
  -v /path/to/your/videos:/media \
  -v scriptorium-models:/models \
  -v scriptorium-state:/state \
  -e WATCH_DIRS=/media -e RUN_ONCE=1 \
  ghcr.io/anastylosis/scriptorium:latest
```

That transcribes whatever is spoken in each file, writes the subtitle beside
it, and exits. The model lands in the `scriptorium-models` volume, so the
download is paid for once. Drop `RUN_ONCE=1` to leave it watching.

## Keep it running on a folder

Get the folder compose file, set the one path, and start it:

```sh
curl -o docker-compose.yml https://raw.githubusercontent.com/Anastylosis/Scriptorium/master/docker-compose.folder.example.yml
$EDITOR docker-compose.yml     # change /path/to/your/videos
docker compose up -d
```

**Asking for a language** is an empty file:

```sh
touch /path/to/your/videos/spanish/subs.en        # this folder and below, in English
touch /path/to/your/videos/spanish/subs.es        # ...and Spanish too
touch /path/to/your/videos/spanish/clip.subs.auto # just clip.mp4, whatever is spoken
```

With no marker anywhere it transcribes whatever is spoken. Use a bare ISO 639
code (`en`, `pt`, `ja`) — never a regional one like `pt-BR`.

**Output** is `<video name>.<lang>.srt` beside the video — `clip.mp4` gets
`clip.en.srt` — which Plex, Jellyfin, Kodi and VLC pick up on their own (a
library server may want a scan first). A ledger in `/state` stops it doing a
file twice, so mount that somewhere that survives the container.

→ [Folder mode in full](docs/folder-mode.md): nested markers, the ledger,
retries, large libraries.

> **If Stash manages the library, use Stash mode.** Folder mode writes the
> file, but only a Stash scan attaches a caption to a scene: you would get
> subtitles on disk, an empty player, and nothing in any log to say why.

## Using it with Stash

Use the `scriptorium` service in `docker-compose.example.yml` as shipped: set
`STASH_URL` (and `STASH_API_KEY` if Stash has authentication on), and mount
your library **at the same container path Stash uses**. If Stash sees
`/data/movie.mp4` from `/volume1/media`, mount `/volume1/media` at `/data`
here. If you cannot, set `PATH_FROM` and `PATH_TO` to map between them.

Then, in Stash:

1. Select scenes and add a tag: `subs:en` for English, `subs:auto` for
   whatever is spoken, or `subs:<code>` for any language. Several tags give
   several files.
2. Wait. It polls every 2 minutes and works one scene at a time.
3. The request tag is swapped for `subs:done` (or `subs:failed`) and the
   folder is rescanned so the caption shows up in the player.

Only `subs:en`, `subs:done` and `subs:failed` are created for you. For
another language, create the tag yourself — `subs:fr`, `subs:pl` — and it is
picked up on the next poll, with no restart. Nothing you have not tagged is
ever read.

- **Use a bare code.** `subs:pt-BR` is refused: Stash cannot attach a caption
  with a regional suffix, so the file would be written and the player would
  stay empty. Use `subs:pt`.
- **A scene is `subs:done` only if every language it asked for was made** or
  already present; otherwise it is `subs:failed` and the log says which
  language and why.
- **Captions missing?** Don't use the per-scene Rescan button; it never
  attaches a subtitle. Scan the directory from Settings → Tasks.

→ [Stash mode in full](docs/stash-mode.md)

## Translating into other languages

Whisper transcribes the spoken language and translates natively only *into*
English. Every other target — English audio to Polish, Japanese to French —
goes through an LLM on Ollama. Start the bundled service with
`docker compose --profile translate up -d`, uncomment `OLLAMA_URL` and
`OLLAMA_MODEL`, and restart; the model is pulled for you.

- The default `translategemma:4b` is under Google's
  [Gemma Terms of Use](https://ai.google.dev/gemma/terms), not an open-source
  licence. `OLLAMA_MODEL=qwen3:8b` (Apache-2.0) works with no other change.
- **The default `large-v3-turbo` cannot translate.** It silently returns the
  source language instead, so non-English audio asked for in English needs
  `OLLAMA_URL` or `TRANSLATE_MODEL=large-v3`; with neither it is skipped and
  logged rather than written wrong.

→ [Translation in full](docs/translation.md): models, speed, reusing existing
transcripts, failures.

## Sharing to MoanSubs

Nothing leaves your machine unless you send it. If you want to share what you
make, [MoanDrop](https://github.com/Anastylosis/MoanDrop) is the client for
[moansubs](https://moansubs.org) — check whether a subtitle already exists
before spending CPU on one:

```sh
moandrop match --lang en --write "Some Scene.mp4"
moandrop push --generated "Some Scene.mp4" "Some Scene.en.srt"
```

→ [More on sharing](docs/generated-subtitles.md#sharing-what-you-make)

## Documentation

- [Folder mode](docs/folder-mode.md) — watching a mount, markers, the ledger
- [Stash mode](docs/stash-mode.md) — setup, tags, language codes, existing captions
- [Translation](docs/translation.md) — Ollama, model choice, turbo and English
- [Generated subtitles](docs/generated-subtitles.md) — the marker, regenerating, VTT, provenance, sharing
- [How it works](docs/how-it-works.md) — routing, hallucination handling, performance, status page
- [Configuration](docs/configuration.md) — every environment variable
- [Troubleshooting](docs/troubleshooting.md)
- [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

## License

Copyright (C) 2026 Wasylq

[GPL-3.0-only](LICENSE).
