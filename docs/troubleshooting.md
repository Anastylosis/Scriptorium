# Troubleshooting

## Captions don't appear after processing

Stash attaches a caption only when a scan walks the *subtitle file itself*. It
rejects `.srt` and `.vtt` as media and then associates them from that
rejection branch, so the scan has to be pointed at a path whose walk reaches
the sidecar.

The per-scene **Rescan** button does not: it scans the video file's own path,
the walk contains one `.mp4`, and the `.srt` beside it is never enumerated.
Nothing is attached, and nothing is logged. Rescanning the scene as many times
as you like will not change that.

Scan the **directory** instead, from Settings → Tasks. Confirm the naming is
exactly `scene.mp4` + `scene.en.srt` — same folder, same basename, bare
two- or three-letter code. A regional code such as `.pt-BR.srt` never attaches;
see [Language codes](stash-mode.md#language-codes).

If the subtitle was written by [folder mode](folder-mode.md), Stash has not
been asked to scan at all: run the library in [Stash mode](stash-mode.md)
instead.

## `Stash HTTP 401`

Authentication is on. Generate an API key in Settings → Security and set
`STASH_API_KEY`.

## GraphQL field errors

Stash's schema shifts between releases. Open
`http://<host>:9999/playground` and check the mutation and filter names against
what the worker sends.

## Path not visible to this container

Stash and the worker must see the same video at the same container path. If
they cannot, set `PATH_FROM` / `PATH_TO` to map between them — see
[Paths must match](stash-mode.md#paths-must-match).

## `WATCH_DIRS and STASH_URL are both set`

One container reads one library. Unset `STASH_URL` for folder mode, or unset
`WATCH_DIRS` to keep talking to Stash.

## `Ollama unreachable` or `Name or service not known`

A DNS error like `Name or service not known` from the translation step means
the worker cannot reach Ollama. The `ollama` service in the example compose
sits behind a profile, so `docker compose up -d` does not start it:

```sh
docker compose --profile translate up -d
```

Setting `OLLAMA_URL` on its own is not enough. The same problem is reported at
startup as `Ollama unreachable at ...`.

The transcript in the spoken language is still written before the translation
is attempted, so nothing but the translation is lost.

## English subtitles are skipped for non-English audio

The default `large-v3-turbo` cannot translate. Set `OLLAMA_URL` or
`TRANSLATE_MODEL=large-v3` — see
[Turbo cannot translate](translation.md#turbo-cannot-translate).

## `DEVICE=cuda` fails on the first scene

There is no GPU path: the published image carries CPU wheels and no cuDNN.
Leave `DEVICE` at `cpu`. See
[Performance](how-it-works.md#performance-and-thread-count).

## A scene comes out badly

Set `MODEL=large-v3` and ask for it again. Recurring junk lines specific to
your files can be added to the hallucination filter in your own image — see
[Hallucination handling](how-it-works.md#hallucination-handling).
