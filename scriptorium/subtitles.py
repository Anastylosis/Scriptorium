"""Rendering cues to a subtitle file.

A cue is a plain `(start, end, text)` tuple of seconds and string.

Generated files carry a short visible marker so a viewer can tell the
subtitles were produced by a machine rather than written by a person. The
marker starts with a fixed sentinel, which also lets a later run recognise
its own output and leave hand-made subtitles alone.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

MARKER = "[scriptorium]"
# stash-subs (this project's former name) shipped subtitles carrying this
# marker. Files written under that name are still out there and must be
# recognised forever — moansubs, the other consumer of this marker, already
# does dual-marker detection on its side.
OLD_MARKER = "[stash-subs]"
SIDECAR_SUFFIX = ".scriptorium.json"

DEFAULT_TEMPLATE = (
    "{marker} machine-generated subtitles · {asr_model}{mt_suffix} · "
    "{languages} · {date}"
)

# How much of each end of a file to search when identifying our own output.
_SNIFF_BYTES = 4096

# How far past the end of the media the marker may run. Subtitle-to-scene
# matching by runtime tolerates about twenty seconds before it treats a
# subtitle as belonging to something else; half of that is ample for a
# three-second marker and leaves the signal intact.
MAX_OVERSHOOT = 10.0


def ts(seconds: float, decimal: str = ",") -> str:
    """SRT separates milliseconds with a comma, WebVTT with a dot."""
    ms = max(0, int(round(seconds * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{decimal}{ms:03d}"


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


def render_vtt(cues, note=None) -> str:
    """WebVTT, whose timestamps use a dot and which has real comments.

    A NOTE block is invisible to every player, so the full provenance can
    travel inside the subtitle file itself rather than in a sidecar.
    """
    out = ["WEBVTT\n\n"]
    if note:
        # A blank line ends a NOTE block, so the payload must not contain one.
        body = re.sub(r"\n\s*\n+", "\n", str(note).strip())
        out.append(f"NOTE\n{body}\n\n")
    for i, (start, end, text) in enumerate(cues, 1):
        out.append(f"{i}\n{ts(start, '.')} --> {ts(end, '.')}\n{cue_text(text)}\n\n")
    return "".join(out)


def render(cues, fmt="srt", note=None) -> str:
    return render_vtt(cues, note=note) if fmt == "vtt" else render_srt(cues)


def write_text(text, dest) -> None:
    """Write atomically — a half-written file scanned by Stash is a bad time."""
    dest = Path(dest)
    tmp = Path(str(dest) + ".part")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, dest)


def write_srt(cues, dest) -> None:
    write_text(render_srt(cues), dest)


_CUE_TIMES = re.compile(
    r"(\d{1,3}):([0-5]\d):([0-5]\d)[.,](\d{1,3})\s*-->\s*"
    r"(\d{1,3}):([0-5]\d):([0-5]\d)[.,](\d{1,3})")


def _seconds(h, m, s, frac):
    # A two-digit fraction is centiseconds, a one-digit is tenths.
    scale = {1: 100, 2: 10, 3: 1}.get(len(frac), 1)
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) * scale / 1000.0


def parse(text: str):
    """Cues from SRT or WebVTT.

    Anchored on the timestamp line, so cue numbers, the WEBVTT header, cue
    identifiers and NOTE blocks are all ignored without needing to know
    which format this is.
    """
    cues = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = _CUE_TIMES.search(lines[i])
        if not m:
            i += 1
            continue
        g = m.groups()
        start, end = _seconds(*g[:4]), _seconds(*g[4:])
        i += 1
        body = []
        while i < len(lines) and lines[i].strip():
            body.append(lines[i])
            i += 1
        text_ = "\n".join(body).strip()
        if text_:
            cues.append((start, end, text_))
    return cues


def without_marker(cues):
    """Drop our own annotation cue.

    Re-using a transcript we wrote must not feed the marker to the
    translator, which would translate it and then have it annotated again.
    Old files may carry the marker this project shipped before it was
    renamed from stash-subs, so both are checked.
    """
    return [c for c in cues if MARKER not in c[2] and OLD_MARKER not in c[2]]


def load(path):
    """Cues from an existing subtitle file, or None if unusable."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    cues = without_marker(parse(text))
    return cues or None


def dest_for(video: Path, lang: str, ext: str = "srt") -> Path:
    stem = video.with_suffix("").name
    return video.parent / f"{stem}.{lang}.{ext}"


@dataclass(frozen=True)
class Provenance:
    tool: str = "scriptorium"
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


    def as_dict(self, **extra):
        d = {"tool": self.tool, "version": self.version,
             "asr_model": self.asr_model, "mt_model": self.mt_model or None,
             "src": self.src, "dst": self.dst, "generated": self.date}
        d.update(extra)
        return d

    def as_json(self, **extra):
        return json.dumps(self.as_dict(**extra), ensure_ascii=False,
                          sort_keys=True)


def sidecar_for(dest):
    return Path(str(dest) + SIDECAR_SUFFIX)


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
    start = last + gap
    end = start + seconds
    if media_duration > 0:
        # Tools that pair subtitles to scenes by runtime allow a margin
        # before a longer subtitle counts against the match, so running a
        # few seconds past the end of the media is harmless. Only a marker
        # long enough to look like a different video needs reining in.
        limit = media_duration + MAX_OVERSHOOT
        if end > limit:
            end = limit
            start = min(start, end - 0.5)
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
    """True when this file carries our marker, current or pre-rename.

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
    return MARKER in blob or OLD_MARKER in blob


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
