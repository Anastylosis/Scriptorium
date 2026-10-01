# Stash mode: taking requests from tags

Tag a scene `subs:en`, `subs:auto`, or any language you like, and it is picked
up on the next poll — the tag is swapped for `subs:done` and the caption
attached. You mark the scenes you want; nothing else in the library is ever
read.

## Setup

Copy `docker-compose.example.yml` to `docker-compose.yml`, point the first
volume at your video library, and start it:

```sh
curl -O https://raw.githubusercontent.com/Anastylosis/Scriptorium/master/docker-compose.example.yml
mv docker-compose.example.yml docker-compose.yml
$EDITOR docker-compose.yml
docker compose up -d
```

Set `STASH_URL` to where the worker can reach Stash (default
`http://stash:9999`). If authentication is on, generate an API key in Stash
under Settings → Security and set `STASH_API_KEY`.

Images are published for `linux/amd64` and `linux/arm64`:

```sh
docker pull ghcr.io/anastylosis/scriptorium:latest
```

### Paths must match

The worker needs to see your videos at the same path Stash reports for them.
If Stash has `/data/movie.mp4` mounted from `/volume1/media`, mount the same
host directory at `/data` here. If you cannot, set `PATH_FROM` (the prefix as
Stash reports it) and `PATH_TO` (the same directory as this container sees
it) to map between the two.

## How you use it

1. In Stash, select one or more scenes (checkbox in the top-left of each card).
2. **Edit** → add a tag:
   - `subs:en` — English subtitles
   - `subs:auto` — subtitles in whatever language is actually spoken
   - `subs:<code>` — any language, see [Language codes](#language-codes)

   You can add more than one; each produces its own file.
3. Wait. The worker polls every 2 minutes (`POLL_SECONDS`) and processes the
   queue one scene at a time.
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

Only `subs:en`, `subs:done` and `subs:failed` are created for you
(`CREATE_TAGS`, `DONE_TAG`, `FAILED_TAG`). To request another language, make
the tag yourself — `subs:fr`, `subs:ja`, `subs:pl` — and it is picked up on
the next poll. No restart, no configuration. To restrict which tags are
honoured instead, see `REQUEST_TAGS`, `TAG_DISCOVERY` and `IGNORE_TAGS` in
[Configuration](configuration.md#stash-mode).

Because the queue *is* a Stash filter, you can watch progress by browsing to the
`subs:en` tag — the list shrinks as work completes.

## Language codes

These rules apply to Stash tags and to [folder-mode markers](folder-mode.md#asking-for-a-language)
alike.

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
reached by translating from the spoken language, which needs `OLLAMA_URL` —
see [Translation](translation.md).

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

If a caption still does not appear, see
[Troubleshooting](troubleshooting.md#captions-dont-appear-after-processing).
