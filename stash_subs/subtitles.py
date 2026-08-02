"""Rendering cues to a subtitle file.

A cue is a plain `(start, end, text)` tuple of seconds and string.

Generated files carry a short visible marker so a viewer can tell the
subtitles were produced by a machine rather than written by a person. The
marker starts with a fixed sentinel, which also lets a later run recognise
its own output and leave hand-made subtitles alone.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

MARKER = "[stash-subs]"

DEFAULT_TEMPLATE = (
    "{marker} machine-generated subtitles · {asr_model}{mt_suffix} · "
    "{languages} · {date}"
)

# How much of each end of a file to search when identifying our own output.
_SNIFF_BYTES = 4096


def ts(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cue_text(text: str) -> str:
    """A blank line ends a cue in SRT, so one inside the text splits it in
    two and every following cue number is wrong."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def render_srt(cues) -> str:
    out = []
    for i, (start, end, text) in enumerate(cues, 1):
        out.append(f"{i}\n{ts(start)} --> {ts(end)}\n{cue_text(text)}\n\n")
    return "".join(out)


def write_srt(cues, dest) -> None:
    """Write atomically — a half-written SRT scanned by Stash is a bad time."""
    dest = Path(dest)
    tmp = Path(str(dest) + ".part")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(render_srt(cues))
    os.replace(tmp, dest)


def dest_for(video: Path, lang: str, ext: str = "srt") -> Path:
    stem = video.with_suffix("").name
    return video.parent / f"{stem}.{lang}.{ext}"


@dataclass(frozen=True)
class Provenance:
    tool: str = "stash-subs"
    version: str = ""
    asr_model: str = ""
    mt_model: str = ""
    src: str = ""
    src_name: str = ""
    dst: str = ""
    dst_name: str = ""
    date: str = ""

    def format_map(self):
        return {
            "marker": MARKER,
            "tool": self.tool,
            "version": self.version,
            "asr_model": self.asr_model,
            "mt_model": self.mt_model,
            # One template covers both routes without a conditional.
            "mt_suffix": f" + {self.mt_model}" if self.mt_model else "",
            "src": self.src,
            "src_name": self.src_name,
            "dst": self.dst,
            "dst_name": self.dst_name,
            # "Spanish → English" when translated, plain "English" when the
            # subtitles are just a transcript of what was spoken.
            "languages": (self.dst_name if self.src == self.dst
                          else f"{self.src_name} → {self.dst_name}"),
            "date": self.date,
        }


class TemplateError(ValueError):
    pass


def validate_template(template: str) -> None:
    """Fail at startup rather than 40 minutes into a transcription."""
    try:
        rendered = template.format_map(Provenance().format_map())
    except KeyError as e:
        raise TemplateError(
            f"unknown placeholder {e} in the annotation template") from None
    except (IndexError, ValueError) as e:
        raise TemplateError(f"malformed annotation template: {e}") from None
    if MARKER not in rendered:
        raise TemplateError(
            f"the annotation template must include {{marker}} (renders as "
            f"{MARKER!r}); without it a later run cannot recognise its own "
            f"output and would overwrite hand-made subtitles")


def annotation_cue(cues, prov, mode="end", seconds=3.0, gap=1.0,
                   media_duration=0.0, template=DEFAULT_TEMPLATE):
    """The marker cue, or None when there is nowhere sensible to put it."""
    if mode == "none" or not cues:
        return None
    text = template.format_map(prov.format_map())

    if mode == "start":
        first = cues[0][0]
        if first < seconds + 0.5:
            return None          # would land on top of the opening line
        return (0.0, min(seconds, first - 0.25), text)

    last = cues[-1][1]
    end = last + gap + seconds
    if media_duration > 0:
        # A subtitle that outlives its video reads as a mismatch to anything
        # pairing subtitles with scenes by runtime, so never overshoot. This
        # bound holds even when it means overlapping the last line of
        # dialogue: an overlap is cosmetic, an overshoot is not.
        end = min(end, media_duration - 0.05)
    start = max(last + gap, end - seconds)
    if start >= end:
        start = max(0.0, end - seconds)
    if end - start < 0.2:
        return None          # nothing usable fits
    return (start, end, text)


def with_annotation(cues, prov, **kw):
    cue = annotation_cue(cues, prov, **kw)
    if cue is None:
        return list(cues)
    if kw.get("mode", "end") == "start":
        return [cue] + list(cues)
    return list(cues) + [cue]


def looks_generated(path) -> bool:
    """True when this file carries our marker.

    Only the head and tail are read, so this stays cheap on a large file and
    works whether the marker was placed at the start or the end.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(_SNIFF_BYTES)
            if size > _SNIFF_BYTES:
                f.seek(max(0, size - _SNIFF_BYTES))
                tail = f.read(_SNIFF_BYTES)
            else:
                tail = b""
    except OSError:
        return False
    blob = (head + tail).decode("utf-8", "replace")
    return MARKER in blob


def should_write(dest, regenerate="never") -> bool:
    """Whether to produce `dest` when something is already there.

    `if-ours` overwrites only files this tool wrote, so a hand-made or
    downloaded subtitle is never destroyed by a re-run.
    """
    dest = Path(dest)
    if not dest.exists():
        return True
    if regenerate == "always":
        return True
    if regenerate == "if-ours":
        return looks_generated(dest)
    return False
