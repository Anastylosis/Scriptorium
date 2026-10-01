# Generated subtitles

## Generated subtitles say so

Every file the worker writes ends with a short cue naming what produced it:

```
[scriptorium] machine-generated subtitles · large-v3-turbo + translategemma:4b · Spanish → English · 2026-08-02
```

A transcript that was not translated names one language rather than two.

It sits **after** the last line of dialogue by default (`ANNOTATE=end`), so it
never covers the opening shot and never overlaps speech. It shows for
`ANNOTATE_SECONDS` (default 3) after a pause of `ANNOTATE_GAP` (default 1)
following the last real cue. On a scene where dialogue runs to the final frame
the marker extends a few seconds past the end of the video, which is
deliberate: tools that pair subtitles to scenes by runtime allow about twenty
seconds of slack, and a marker crammed backwards over the closing line would
be worse. A marker long enough to distort that signal is reined in.

Set `ANNOTATE=start` to put it first instead — useful if you want to know what
made a file before watching it. It is skipped automatically when dialogue
begins too early to fit. `ANNOTATE=none` turns it off, and produces output
byte-identical to having never enabled it.

`ANNOTATE_TEXT` takes a template. Available placeholders: `{marker}`, `{tool}`,
`{version}`, `{asr_model}`, `{mt_model}`, `{mt_suffix}`, `{src}`, `{src_name}`,
`{dst}`, `{dst_name}`, `{languages}`, `{date}`. It must contain `{marker}`, and
is checked when the container starts rather than part-way through a
transcription. The built-in template is:

```
{marker} machine-generated subtitles · {asr_model}{mt_suffix} · {languages} · {date}
```

## Regenerating

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

## VTT and provenance

`OUTPUT_FORMATS=vtt` writes WebVTT instead of SubRip; `OUTPUT_FORMATS=srt,vtt`
writes both, which gives Stash two caption tracks for the same language.

WebVTT has real comments, so a `NOTE` block at the top carries the full
provenance as JSON — the models, the languages, the date, the cue count. It is
invisible to every player. The visible marker cue is still written; the note is
extra.

`ANNOTATE_SIDECAR=1` writes the same JSON to `<subtitle>.scriptorium.json`. Off
by default, because it puts a second file in your media folder to record
something the marker already says. Stash ignores the extension.

The exact wire format of the marker and the JSON is specified in
[spec/provenance/SPEC.md](../spec/provenance/SPEC.md).

## Sharing what you make

Scriptorium is self-contained: no account, no API key, nothing leaves the
machine but the requests you configure to your own Stash and your own Ollama.
Sharing a subtitle it made is a separate tool and a deliberate step, and
nothing here changes if you never take it.

[moansubs](https://moansubs.org) is a subtitle database that identifies a
video by what it *is* rather than what it is called, and
[MoanDrop](https://github.com/Anastylosis/MoanDrop) is its client:

```sh
moandrop match --lang en --write "Some Scene.mp4"    # is one already out there?
moandrop push --generated "Some Scene.mp4" "Some Scene.en.srt"
```

Worth doing in that order. A subtitle somebody typed out by hand beats
anything transcribed from the audio, and finding one saves you the CPU time.

The marker Scriptorium writes into every file it generates is also how
moansubs labels a track as machine-made, so a subtitle from here is labelled
as such whether or not the uploader remembers the flag.
