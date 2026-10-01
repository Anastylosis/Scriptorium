# Folder mode: watching a mount without Stash

Set `WATCH_DIRS` and there is no Stash in the picture: the worker walks the
mount, transcribes what it finds, and writes the subtitle beside the video.
Nothing else changes — same models, same marker, same status page.

[`docker-compose.folder.example.yml`](../docker-compose.folder.example.yml)
is the whole setup: mount your videos at `/media`, keep `/state` on a volume
(it holds the ledger), and `WATCH_DIRS=/media` switches folder mode on.

`WATCH_DIRS` takes several directories, comma-separated.

`STASH_URL` must be left unset — it has a default, so setting both is refused
rather than guessed at.

The subtitle is written as `<video stem>.<lang>.srt` beside the video, which
is the sidecar convention Plex, Jellyfin, Kodi and VLC all pick up with no
extra step — though a library server generally wants a scan before it notices.

> **If Stash manages this library, use [Stash mode](stash-mode.md).** Folder
> mode writes the subtitle correctly and then stops, because attaching a
> caption to a scene is something only a Stash scan does. You would get files
> on disk, a player with no subtitle track, and nothing in any log to say why.
> Folder mode is for libraries Stash has never heard of.

Stash-only settings — `PATH_FROM`, `PATH_TO`, `REQUEST_TAGS`, `STASH_API_KEY`,
`TAG_DISCOVERY`, `CREATE_TAGS`, `IGNORE_TAGS`, `DONE_TAG` and `FAILED_TAG` —
are named at startup and ignored, so a compose file carried across from a
Stash setup does not quietly half-work.

## Asking for a language

What a tag does in Stash, an empty file does here:

```
/media/spanish/subs.en          everything under /media/spanish wants English
/media/spanish/subs.es          ...and Spanish; several markers may sit together
/media/spanish/otra.subs.auto   just this video, the language actually spoken
```

`touch subs.en` is the whole gesture. The **nearest** marker wins: one deeper
in the tree replaces the one above it rather than adding to it, and a video
with its own `<name>.subs.<lang>` ignores its directory entirely. A per-video
marker may name the video with or without its extension — `otra.subs.en` and
`otra.mkv.subs.en` both work. With no marker anywhere, `WATCH_LANGS` decides —
and its default is `auto`, meaning transcribe whatever is spoken.

The code follows the same rules as a tag: a bare two- or three-letter ISO 639
subtag, so `subs.pt-BR` is refused and logged. See
[Language codes](stash-mode.md#language-codes).

A language other than English or the one being spoken needs an LLM; see
[Translation](translation.md).

## What stops it doing the same file twice

`subs:done` has no equivalent on a filesystem, so the worker keeps a ledger
(`ledger.json`) in `STATE_DIR`. A video is offered again when it changes, when
a marker asks for a language it has not been asked for before — so `touch
subs.es` in a finished directory re-opens every video in it — or when a
subtitle it wrote is deleted. A failure is remembered the way `subs:failed`
is; `WATCH_RETRY_FAILED=1` retries anyway, and deleting the ledger costs a
re-detection, never a subtitle. Entries for videos that are no longer there
are dropped as they are noticed, so the file stays about as long as your
library rather than growing forever. A directory that suddenly reads as empty
— an unmounted share — does not count as the videos being gone.

Subtitles already sitting beside a video are read the way Stash's caption list
is, so a hand-made `movie.eng.srt` still means `movie.en.srt` is pointless,
and [`REGENERATE`](generated-subtitles.md#regenerating) behaves exactly as it
does with Stash.

## Files still arriving

A file whose mtime is less than `WATCH_MIN_AGE` seconds old (default 60) is
left alone — half a copied-in mkv transcribes as half a film, and nothing
would come back to finish it.

Only files with an extension in `WATCH_EXTENSIONS` are looked at: by default
`mp4`, `mkv`, `m4v`, `mov`, `avi`, `webm`, `wmv`, `flv`, `ts`, `mpg`, `mpeg`.

## Large libraries

Every poll walks the whole tree, so on a large library on spinning disks or a
network mount, raise `POLL_SECONDS` — an hour is a reasonable interval for a
folder you add to occasionally. Mount `/state` somewhere real: the ledger is
what stops a rebuilt container listening to the entire library again.

All settings are in [Configuration](configuration.md#folder-mode).
