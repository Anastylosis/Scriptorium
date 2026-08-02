"""Rendering cues to a subtitle file.

A cue is a plain `(start, end, text)` tuple of seconds and string.
"""

import os
import re
from pathlib import Path


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
