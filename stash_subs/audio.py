"""Probing duration and cutting the sample used for language detection."""

import logging
import os
import subprocess

log = logging.getLogger(__name__)


def probe_duration(path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def sample(path, duration, out_wav) -> None:
    """Concatenate three 45s chunks from 25/50/75% into one wav.

    Whisper's built-in detection only looks at the first 30 seconds, which
    in a lot of libraries is music, an intro card, or silence.
    """
    points = [duration * f for f in (0.25, 0.5, 0.75)] if duration > 300 else [0.0]
    parts = []
    for i, start in enumerate(points):
        p = f"{out_wav}.{i}.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", str(int(start)), "-t", "45",
             "-i", str(path), "-ac", "1", "-ar", "16000", "-vn", p],
            check=True, timeout=300,
        )
        parts.append(p)
    if len(parts) == 1:
        os.replace(parts[0], out_wav)
        return
    lst = f"{out_wav}.txt"
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", out_wav], check=True, timeout=300)
    for p in parts + [lst]:
        try:
            os.remove(p)
        except OSError:
            pass
